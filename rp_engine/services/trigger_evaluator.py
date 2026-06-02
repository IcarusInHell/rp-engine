"""Situational trigger evaluation engine.

Evaluates conditions defined on triggers against:
- Expression functions (any, all, none, near, count, seq) on text
- State conditions (character attributes, relationships, scene state)
- Signal conditions (scene classifier output)

Tier 1 implementation: individual conditions with match_mode (any/all).
Full infix parser deferred to fast-follow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rp_engine.config import get_config
from rp_engine.database import Database
from rp_engine.models.trigger import ConditionResult, TriggerTestResult
from rp_engine.services.state_entry_resolver import (
    latest_character_state,
    latest_scene_state,
)
from rp_engine.utils.json_helpers import safe_parse_json_array
from rp_engine.utils.stemmer import stem, tokenize

logger = logging.getLogger(__name__)

# Regex for parsing expression function calls: func("arg1","arg2",N)
_EXPR_PATTERN = re.compile(
    r'^(\w+)\s*\(\s*(.*?)\s*\)$', re.DOTALL
)
# Parse quoted string arguments
_ARG_PATTERN = re.compile(r'"([^"]*)"')

# Allowlists for dynamic column selection in _eval_state
_VALID_CHAR_FIELDS = {"location", "conditions", "emotional_state", "last_seen"}
_VALID_REL_FIELDS = {"initial_trust_score", "trust_modification_sum", "dynamic", "trust_stage"}
_VALID_SCENE_FIELDS = {"location", "time_of_day", "mood", "in_story_timestamp"}


@dataclass
class FiredTrigger:
    trigger_id: str
    trigger_name: str
    inject_type: str
    inject_content: str | None
    inject_card_path: str | None
    priority: int
    matched_conditions: list[str]


class TriggerEvaluator:
    """Evaluate situational triggers against text, state, and signals."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def evaluate_all(
        self,
        rp_folder: str,
        branch: str,
        text: str,
        signals: dict[str, float],
        current_turn: int,
    ) -> list[FiredTrigger]:
        """Load enabled triggers, evaluate each, return fired ones sorted by priority.

        Firing model (5a):
        - **delay_turns** N: a condition-driven fire requires N consecutive
          matching turns (0/1 = fire on first match, the pre-5a behavior).
        - **sticky_turns** N: after a condition-driven fire, the trigger keeps
          re-injecting for N-1 further turns even without a fresh match
          (1 = no persistence, the pre-5a behavior).
        - **cooldown_turns** still gates fresh condition-driven fires; sticky
          re-injection rides through it (it's the same fire persisting).
        Defaults (sticky=1, delay=0, stemming aside) reproduce the old behavior.
        """
        stem_enabled = get_config().prompt.trigger_stemming
        rows = await self.db.fetch_all(
            """SELECT id, name, conditions, match_mode, inject_type,
                      inject_content, inject_card_path, priority,
                      cooldown_turns, last_fired_turn,
                      sticky_turns, delay_turns, consecutive_matches
               FROM situational_triggers
               WHERE rp_folder = ? AND enabled = 1""",
            [rp_folder],
        )

        fired: list[FiredTrigger] = []
        for row in rows:
            last_fired = row.get("last_fired_turn")
            cooldown = row.get("cooldown_turns", 0) or 0
            sticky_turns = row.get("sticky_turns") or 1
            delay_turns = row.get("delay_turns") or 0
            prev_consecutive = row.get("consecutive_matches") or 0

            conditions = safe_parse_json_array(row.get("conditions"))
            if not conditions:
                logger.warning("Invalid conditions JSON for trigger %s", row["id"])
                continue

            match_mode = row.get("match_mode", "any")
            matched_descs: list[str] = []
            all_pass = True       # stemming-union result
            all_pass_raw = True   # exact-only result
            any_pass = False
            any_pass_raw = False

            # Evaluate ALL conditions (no early break) so a stem-only fire is
            # detectable by comparing the union result against the raw result.
            for cond in conditions:
                passed, passed_raw, detail = await self._evaluate_condition(
                    cond, text, signals, rp_folder, branch, stem_enabled
                )
                if passed:
                    matched_descs.append(detail)
                    any_pass = True
                else:
                    all_pass = False
                if passed_raw:
                    any_pass_raw = True
                else:
                    all_pass_raw = False

            matched_now = (
                (match_mode == "any" and any_pass) or
                (match_mode == "all" and all_pass)
            )
            matched_raw = (
                (match_mode == "any" and any_pass_raw) or
                (match_mode == "all" and all_pass_raw)
            )

            # --- delay: require N consecutive matching turns ---
            new_consecutive = prev_consecutive + 1 if matched_now else 0
            delay_satisfied = new_consecutive >= max(1, delay_turns)

            cooldown_blocks = (
                last_fired is not None and cooldown > 0
                and current_turn - last_fired < cooldown
            )
            condition_fire = matched_now and delay_satisfied and not cooldown_blocks

            # --- sticky: re-inject within N turns of the last condition-driven fire ---
            sticky_active = (
                not condition_fire and sticky_turns > 1
                and last_fired is not None
                and 0 < current_turn - last_fired < sticky_turns
            )

            if condition_fire:
                # A fire is "stem-only" when the union matched but exact would not.
                if stem_enabled and not matched_raw:
                    logger.warning(
                        "Trigger %s (%s) fired ONLY due to stemmed matching "
                        "(exact match would not fire): %s",
                        row["id"], row["name"], matched_descs,
                    )
                fired.append(FiredTrigger(
                    trigger_id=row["id"],
                    trigger_name=row["name"],
                    inject_type=row["inject_type"],
                    inject_content=row.get("inject_content"),
                    inject_card_path=row.get("inject_card_path"),
                    priority=row.get("priority", 0) or 0,
                    matched_conditions=matched_descs,
                ))
                future = await self.db.enqueue_write(
                    "UPDATE situational_triggers SET last_fired_turn = ? WHERE id = ?",
                    [current_turn, row["id"]],
                )
                await future
            elif sticky_active:
                fired.append(FiredTrigger(
                    trigger_id=row["id"],
                    trigger_name=row["name"],
                    inject_type=row["inject_type"],
                    inject_content=row.get("inject_content"),
                    inject_card_path=row.get("inject_card_path"),
                    priority=row.get("priority", 0) or 0,
                    matched_conditions=[f"sticky: re-injected (turn {current_turn})"],
                ))
                # Sticky does NOT update last_fired_turn — the window is anchored
                # to the original condition-driven fire.

            # Persist the consecutive counter only when the delay feature is in
            # use (keeps the common case write-light and byte-identical).
            if delay_turns > 1 and new_consecutive != prev_consecutive:
                future = await self.db.enqueue_write(
                    "UPDATE situational_triggers SET consecutive_matches = ? WHERE id = ?",
                    [new_consecutive, row["id"]],
                )
                await future

        # Sort by priority (highest first)
        fired.sort(key=lambda t: t.priority, reverse=True)
        return fired

    async def evaluate_single(
        self,
        trigger_id: str,
        text: str,
        signals: dict[str, float],
        rp_folder: str | None = None,
        branch: str = "main",
    ) -> TriggerTestResult:
        """For /test endpoint. Returns detailed per-condition results."""
        row = await self.db.fetch_one(
            "SELECT conditions, match_mode, rp_folder FROM situational_triggers WHERE id = ?",
            [trigger_id],
        )
        if not row:
            return TriggerTestResult(
                would_fire=False,
                conditions_evaluated=[],
                signals=signals,
            )

        rp = rp_folder or row.get("rp_folder", "")
        match_mode = row.get("match_mode", "any")

        conditions = safe_parse_json_array(row.get("conditions"))
        stem_enabled = get_config().prompt.trigger_stemming

        results: list[ConditionResult] = []
        for i, cond in enumerate(conditions):
            passed, _passed_raw, detail = await self._evaluate_condition(
                cond, text, signals, rp, branch, stem_enabled
            )
            results.append(ConditionResult(
                condition_index=i,
                condition_type=cond.get("type", "unknown"),
                matched=passed,
                detail=detail,
            ))

        any_matched = any(r.matched for r in results)
        all_matched = all(r.matched for r in results) and len(results) > 0

        would_fire = (
            (match_mode == "any" and any_matched) or
            (match_mode == "all" and all_matched)
        )

        return TriggerTestResult(
            would_fire=would_fire,
            conditions_evaluated=results,
            signals=signals,
        )

    async def _evaluate_condition(
        self,
        cond: dict,
        text: str,
        signals: dict[str, float],
        rp_folder: str,
        branch: str,
        stem_enabled: bool,
    ) -> tuple[bool, bool, str]:
        """Evaluate a single condition.

        Returns ``(passed, passed_raw, detail)`` where ``passed`` applies the
        stemming union (when ``stem_enabled``) and ``passed_raw`` is the
        exact-match-only result. They differ only for ``expression`` conditions;
        ``state``/``signal`` conditions never stem, so the two are identical.
        Comparing the two lets the caller flag a *stem-only* fire (over-firing is
        diagnosable, not silent).
        """
        cond_type = cond.get("type", "")

        if cond_type == "expression":
            return self._eval_expression(cond.get("expr", ""), text, stem_enabled)
        elif cond_type == "state":
            passed, detail = await self._eval_state(
                cond.get("path", ""),
                cond.get("operator", "=="),
                cond.get("value"),
                cond.get("values"),
                rp_folder,
                branch,
            )
            return passed, passed, detail
        elif cond_type == "signal":
            passed, detail = self._eval_signal(
                cond.get("signal", ""),
                cond.get("operator", ">="),
                cond.get("value", 0),
                signals,
            )
            return passed, passed, detail

        return False, False, f"Unknown condition type: {cond_type}"

    def _eval_expression(
        self, expr: str, text: str, stem_enabled: bool,
    ) -> tuple[bool, bool, str]:
        """Evaluate expression functions against text.

        Returns ``(passed, passed_raw, detail)``. Stemming is applied as a
        **union** with raw substring matching: an argument is "present" if it
        appears as a raw substring **or** (single-token args only) its stem is in
        the text's stem set. This makes default-on stemming purely additive for
        positive matchers — a match that fired before still fires. ``none()`` is
        the one inversion: a stemmed presence can flip it from pass to fail
        (correctly — "none of these concepts"), so its stem-only result is a
        *suppression*, surfaced via ``passed_raw``.
        """
        if not expr:
            return False, False, "Empty expression"

        match = _EXPR_PATTERN.match(expr.strip())
        if not match:
            return False, False, f"Invalid expression syntax: {expr}"

        func_name = match.group(1).lower()
        args_str = match.group(2)

        # Parse quoted arguments
        str_args = _ARG_PATTERN.findall(args_str)
        text_lower = text.lower()
        text_stems = _stem_set(text) if stem_enabled else frozenset()

        if func_name == "any":
            raw = any(_present_raw(a, text_lower) for a in str_args)
            full = raw or (
                stem_enabled and any(_present_stem(a, text_stems) for a in str_args)
            )
            return full, raw, f"any() match={full} (raw={raw}) in {str_args}"

        elif func_name == "all":
            if not str_args:
                return False, False, "all() requires args"
            raw = all(_present_raw(a, text_lower) for a in str_args)
            full = all(
                _present_raw(a, text_lower)
                or (stem_enabled and _present_stem(a, text_stems))
                for a in str_args
            )
            return full, raw, f"all() match={full} (raw={raw}): {str_args}"

        elif func_name == "none":
            raw_found = any(_present_raw(a, text_lower) for a in str_args)
            full_found = raw_found or (
                stem_enabled and any(_present_stem(a, text_stems) for a in str_args)
            )
            return (not full_found), (not raw_found), (
                f"none() absent={not full_found} (raw_absent={not raw_found}): {str_args}"
            )

        elif func_name == "near":
            if len(str_args) < 2:
                return False, False, "near() requires at least 2 string args"
            distance = 200  # default
            remaining = args_str
            for a in str_args:
                remaining = remaining.replace(f'"{a}"', "", 1)
            nums = re.findall(r'\d+', remaining)
            if nums:
                distance = int(nums[0])
            w1, w2 = str_args[0], str_args[1]

            def _near(use_stem: bool) -> bool:
                i1 = _first_index(w1, text_lower, use_stem)
                i2 = _first_index(w2, text_lower, use_stem)
                return i1 != -1 and i2 != -1 and abs(i1 - i2) <= distance

            raw = _near(False)
            full = raw or (stem_enabled and _near(True))
            return full, raw, f"near('{w1}','{w2}',{distance}) match={full} (raw={raw})"

        elif func_name == "count":
            if not str_args:
                return False, False, "count() requires a word argument"
            word = str_args[0]
            n_raw = text_lower.count(word.lower())
            n_full = max(n_raw, _stem_count(word, text)) if stem_enabled else n_raw
            remaining = args_str
            for a in str_args:
                remaining = remaining.replace(f'"{a}"', "", 1)
            comp = re.search(r'([><=!]+)\s*(\d+)', remaining)
            if comp:
                op, val = comp.group(1), int(comp.group(2))
                raw = _compare(n_raw, op, val)
                full = _compare(n_full, op, val)
                return full, raw, f"count('{word}') = {n_full} (raw={n_raw}) {op} {val}: {full}"
            return (n_full > 0), (n_raw > 0), f"count('{word}') = {n_full} (raw={n_raw})"

        elif func_name == "seq":
            if len(str_args) < 2:
                return False, False, "seq() requires 2 arguments"
            w1, w2 = str_args[0], str_args[1]

            def _seq(use_stem: bool) -> bool:
                i1 = _first_index(w1, text_lower, use_stem)
                i2 = _first_index(w2, text_lower, use_stem)
                return i1 != -1 and i2 != -1 and i1 < i2

            raw = _seq(False)
            full = raw or (stem_enabled and _seq(True))
            return full, raw, f"seq('{w1}','{w2}') match={full} (raw={raw})"

        return False, False, f"Unknown function: {func_name}"

    async def _eval_state(
        self,
        path: str,
        operator: str,
        value,
        values: list | None,
        rp_folder: str,
        branch: str,
    ) -> tuple[bool, str]:
        """Evaluate state conditions by querying the database.

        Path formats:
        - characters.{name}.{field}
        - relationships.{a}->{b}.{field}
        - scene.{field}
        """
        if not path:
            return False, "Empty state path"

        parts = path.split(".")

        if parts[0] == "characters" and len(parts) >= 3:
            name = parts[1]
            field_name = parts[2]
            if field_name not in _VALID_CHAR_FIELDS:
                logger.warning("Invalid character field in trigger condition: %s", field_name)
                return False, f"Invalid character field: {field_name}"
            # Look up card_id from story_cards, then query character_state_entries
            card_id = await self.db.fetch_val(
                "SELECT id FROM story_cards WHERE LOWER(name) = ? AND rp_folder = ?",
                [name.lower(), rp_folder],
            )
            if not card_id:
                return False, f"Character '{name}' not found"
            row = await latest_character_state(self.db, rp_folder, branch, card_id)
            if not row:
                return False, f"Character '{name}' has no state"

            db_val = row.get(field_name)
            return self._compare_state_value(db_val, operator, value, values, field_name)

        elif parts[0] == "relationships" and len(parts) >= 3:
            # Format: relationships.a->b.field
            rel_part = parts[1]
            field_name = parts[2]
            if "->" not in rel_part:
                return False, f"Invalid relationship path: {rel_part}"
            char_a, char_b = rel_part.split("->", 1)

            if field_name == "trust_score":
                baseline = await self.db.fetch_val(
                    """SELECT baseline_score FROM trust_baselines
                       WHERE LOWER(character_a) = ? AND LOWER(character_b) = ?
                         AND rp_folder = ? AND branch = ?""",
                    [char_a.lower(), char_b.lower(), rp_folder, branch],
                )
                mod_sum = await self.db.fetch_val(
                    """SELECT COALESCE(SUM(change), 0) FROM trust_modifications
                       WHERE LOWER(character_a) = ? AND LOWER(character_b) = ?
                         AND rp_folder = ? AND branch = ?""",
                    [char_a.lower(), char_b.lower(), rp_folder, branch],
                )
                db_val = (baseline or 0) + (mod_sum or 0)
                return self._compare_state_value(db_val, operator, value, values, field_name)
            else:
                if field_name not in _VALID_REL_FIELDS:
                    logger.warning("Invalid relationship field in trigger condition: %s", field_name)
                    return False, f"Invalid relationship field: {field_name}"
                # For non-trust_score relationship fields, check trust_baselines
                row = await self.db.fetch_one(
                    f"""SELECT {field_name} FROM trust_baselines
                        WHERE LOWER(character_a) = ? AND LOWER(character_b) = ?
                          AND rp_folder = ? AND branch = ?""",
                    [char_a.lower(), char_b.lower(), rp_folder, branch],
                )

            if not row:
                return False, f"Relationship '{char_a}->{char_b}' not found"

            db_val = row.get(field_name)
            return self._compare_state_value(db_val, operator, value, values, field_name)

        elif parts[0] == "scene" and len(parts) >= 2:
            field_name = parts[1]
            if field_name not in _VALID_SCENE_FIELDS:
                logger.warning("Invalid scene field in trigger condition: %s", field_name)
                return False, f"Invalid scene field: {field_name}"
            row = await latest_scene_state(self.db, rp_folder, branch)
            if not row:
                return False, "No scene context"

            db_val = row.get(field_name)
            return self._compare_state_value(db_val, operator, value, values, field_name)

        return False, f"Unknown state path prefix: {parts[0]}"

    def _compare_state_value(
        self, db_val, operator: str, value, values: list | None, field_name: str
    ) -> tuple[bool, str]:
        """Compare a DB value against condition using operator."""
        # Handle JSON columns (conditions, behavioral_modifiers)
        if isinstance(db_val, str) and db_val.startswith("["):
            db_val = safe_parse_json_array(db_val) or db_val

        if operator == "contains":
            if isinstance(db_val, list):
                result = value in db_val or (
                    isinstance(value, str) and
                    value.lower() in [str(v).lower() for v in db_val]
                )
                return result, f"{field_name} contains '{value}': {result}"
            if isinstance(db_val, str):
                result = str(value).lower() in db_val.lower()
                return result, f"{field_name} contains '{value}': {result}"
            return False, f"{field_name} not iterable"

        if operator == "intersects":
            if isinstance(db_val, list) and isinstance(values, list):
                db_lower = {str(v).lower() for v in db_val}
                val_lower = {str(v).lower() for v in values}
                result = bool(db_lower & val_lower)
                return result, f"{field_name} intersects {values}: {result}"
            return False, f"{field_name} not a list"

        if operator == "in":
            if isinstance(values, list):
                result = db_val in values or (
                    isinstance(db_val, str) and
                    db_val.lower() in [str(v).lower() for v in values]
                )
                return result, f"{field_name} in {values}: {result}"
            return False, "No values list for 'in' operator"

        # Numeric / string comparison
        try:
            if isinstance(value, (int, float)) and db_val is not None:
                db_num = float(db_val)
                result = _compare(db_num, operator, float(value))
                return result, f"{field_name} = {db_num} {operator} {value}: {result}"
        except (ValueError, TypeError):
            pass

        # String comparison
        result = _compare(db_val, operator, value)
        return result, f"{field_name} {operator} {value}: {result}"

    def _eval_signal(
        self,
        signal_name: str,
        operator: str,
        value: float,
        signals: dict[str, float],
    ) -> tuple[bool, str]:
        """Compare signal score against threshold."""
        score = signals.get(signal_name, 0.0)
        result = _compare(score, operator, value)
        return result, f"signal.{signal_name} = {score:.2f} {operator} {value}: {result}"


# Token scan with character offsets, for positional matchers (near/seq).
_TOKEN_SCAN = re.compile(r"[\w'-]+")


def _stem_set(text: str) -> frozenset[str]:
    """Stemmed token set for a body of text (membership tests)."""
    return frozenset(stem(t) for t in tokenize(text))


def _present_raw(arg: str, text_lower: str) -> bool:
    """Raw substring presence — the pre-5a behavior."""
    return arg.lower() in text_lower


def _present_stem(arg: str, text_stems: frozenset[str]) -> bool:
    """Stemmed-token presence — single-token args only (n-grams stay exact,
    matching the Phase 0 entity_extractor precedent: stem unigrams, never phrases).
    """
    toks = tokenize(arg)
    if len(toks) != 1:
        return False
    return stem(toks[0]) in text_stems


def _stem_count(word: str, text: str) -> int:
    """Count occurrences of ``word``'s stem among the text's stemmed tokens."""
    toks = tokenize(word)
    if len(toks) != 1:
        return 0
    target = stem(toks[0])
    return sum(1 for t in tokenize(text) if stem(t) == target)


def _first_index(word: str, text_lower: str, use_stem: bool) -> int:
    """First character offset of ``word`` in ``text``.

    Exact mode: raw substring ``.find``. Stem mode (single-token args only):
    the start offset of the first whole token whose stem matches ``word``'s stem
    — additive, only consulted when the raw find already failed.
    """
    if not use_stem:
        return text_lower.find(word.lower())
    toks = tokenize(word)
    if len(toks) != 1:
        return text_lower.find(word.lower())
    target = stem(toks[0])
    for m in _TOKEN_SCAN.finditer(text_lower):
        if stem(m.group(0)) == target:
            return m.start()
    return -1


def _compare(a, op: str, b) -> bool:
    """Generic comparison operator."""
    if a is None:
        return False
    try:
        if op == "==":
            return a == b
        elif op == "!=":
            return a != b
        elif op == "<":
            return a < b
        elif op == ">":
            return a > b
        elif op == "<=":
            return a <= b
        elif op == ">=":
            return a >= b
    except TypeError:
        return False
    return False
