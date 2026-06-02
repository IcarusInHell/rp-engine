"""In-story clock advancement — no LLM, regex + heuristics.

Ported from .claude/hooks/timestamp-calculator.js (743 lines).
Detects activities in response text, calculates elapsed time using
parallel/sequential category logic, and advances the in-story timestamp.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from rp_engine.database import Database
from rp_engine.models.analysis import TimeAdvanceResponse
from rp_engine.models.state import SceneUpdate
from rp_engine.services.state_entry_resolver import latest_scene_state
from rp_engine.services.state_manager import StateManager

logger = logging.getLogger(__name__)

DEFAULT_DURATION = 5
MIN_DURATION = 1

# ---------------------------------------------------------------------------
# Activity categories (same-category = sequential/sum, different = parallel/max)
# Combat is NEVER parallel — always adds to total.
# Daily activities are always sequential with other categories.
# ---------------------------------------------------------------------------

ACTIVITY_CATEGORIES: dict[str, list[str]] = {
    "conversation": ["talk", "chat", "argue", "confess", "lecture"],
    "movement": ["walk", "run", "climb", "sneak", "swim", "ride"],
    "combat": ["fight", "battle", "spar", "escape"],
    "daily": ["eat", "drink", "cook", "bathe", "dress", "sleep", "nap"],
    "emotional": ["cry", "laugh", "hug", "kiss", "cuddle", "comfort"],
    "work": ["study", "write", "read", "repair", "train"],
    "explore": ["explore", "search", "investigate"],
}

# Base duration in minutes per activity
DURATION_TABLE: dict[str, int] = {
    "walk": 15,
    "run": 8,
    "climb": 10,
    "sneak": 10,
    "swim": 15,
    "ride": 20,
    "talk": 10,
    "chat": 15,
    "argue": 10,
    "confess": 15,
    "lecture": 20,
    "fight": 5,
    "battle": 15,
    "spar": 10,
    "escape": 5,
    "eat": 30,
    "drink": 10,
    "cook": 30,
    "bathe": 20,
    "dress": 5,
    "sleep": 420,
    "nap": 60,
    "cry": 10,
    "laugh": 5,
    "hug": 5,
    "kiss": 5,
    "cuddle": 15,
    "comfort": 10,
    "study": 30,
    "write": 20,
    "read": 30,
    "repair": 30,
    "train": 30,
    "explore": 30,
    "search": 20,
    "investigate": 25,
}

# ---------------------------------------------------------------------------
# Activity regex patterns
# ---------------------------------------------------------------------------

ACTIVITY_PATTERNS: dict[str, re.Pattern] = {
    "walk": re.compile(
        r"\b(?:she|he|they|i)\s+(?:walk|walked|walking|walks|stroll|strolled)\b", re.I
    ),
    "run": re.compile(
        r"\b(?:she|he|they|i)\s+(?:run|ran|running|runs|sprint|sprinted)\b", re.I
    ),
    "climb": re.compile(r"\b(?:climb|climbed|climbing)\s+(?:up|down|over|the|a)", re.I),
    "sneak": re.compile(
        r"\b(?:sneak|sneaked|sneaking|crept|creeping)\s+(?:through|into|past|around)",
        re.I,
    ),
    "eat": re.compile(
        r"\b(?:eat|ate|eating)\s+(?:the|a|some|breakfast|lunch|dinner|food|meal)", re.I
    ),
    "drink": re.compile(
        r"\b(?:drink|drank|drinking|sip|sipped)\s+(?:the|a|some|from)", re.I
    ),
    "sleep": re.compile(r"\b(?:sleep|slept|sleeping|slumber|slumbered)\b", re.I),
    "bathe": re.compile(r"\b(?:bathe|bathed|bathing|shower|showered)\b", re.I),
    "fight": re.compile(r"\b(?:fight|fought|fighting|attack|attacked|attacking)\s", re.I),
    "battle": re.compile(r"\b(?:battle|battled|battling)\b", re.I),
    "read": re.compile(r"\b(?:read|reading)\s+(?:the|a|through|over)", re.I),
    "write": re.compile(r"\b(?:write|wrote|writing)\s+(?:a|the|down)", re.I),
    "explore": re.compile(r"\b(?:explore|explored|exploring)\s+(?:the|a)", re.I),
    "search": re.compile(r"\b(?:search|searched|searching)\s+(?:the|for|through)", re.I),
}

# ---------------------------------------------------------------------------
# Negation patterns (check 30 chars before match)
# ---------------------------------------------------------------------------

_NEGATION = re.compile(
    r"\b(?:not|don't|doesn't|didn't|cannot|can't|couldn't|won't|wouldn't|never|"
    r"do\s+not|does\s+not|did\s+not)\s*$",
    re.I,
)

# ---------------------------------------------------------------------------
# Modifiers (most extreme wins)
# ---------------------------------------------------------------------------

@dataclass
class Modifier:
    """A pace modifier — its name, time multiplier, and the regex that detects it in response text."""

    name: str
    multiplier: float
    pattern: re.Pattern


MODIFIERS = [
    Modifier("rushed", 0.6, re.compile(
        r"\b(?:desperately|frantically|racing|emergency|immediately|now)\b", re.I
    )),
    Modifier("fast", 0.75, re.compile(
        r"\b(?:quickly|hurriedly|briskly|swiftly|hastily)\b", re.I
    )),
    Modifier("relaxed", 1.25, re.compile(
        r"\b(?:leisurely|comfortably|casually|at\s+ease|unhurried)\b", re.I
    )),
    Modifier("slow", 1.5, re.compile(
        r"\b(?:slowly|carefully|cautiously|deliberately|hesitantly)\b", re.I
    )),
    Modifier("thorough", 2.0, re.compile(
        r"\b(?:thoroughly|meticulously|exhaustively|comprehensively|completely)\b", re.I
    )),
]

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

_TIMESTAMP_RE = re.compile(
    r"\[(\w+),\s+(\w+)\s+(\d+),\s+(\d+)\s+-\s+(\d+):(\d+)\s+(AM|PM)(?:,\s+(.+?))?\]"
)

MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

MONTH_LIST = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# Days per month (Feb always 28 — no leap year handling for RP purposes)
DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

WEEKDAYS = [
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"
]


class TimestampTracker:
    """Detect activities in response text and advance in-story clock."""

    def __init__(self, db: Database, state_manager: StateManager | None = None) -> None:
        self.db = db
        self.state_manager = state_manager

    async def advance_time(
        self,
        response_text: str,
        rp_folder: str,
        branch: str = "main",
        override_minutes: int | None = None,
    ) -> TimeAdvanceResponse:
        """Detect activities, calculate duration, advance timestamp.

        If override_minutes is set, skip activity detection.
        """
        # Load current timestamp from CoW scene_state_entries
        row = await latest_scene_state(self.db, rp_folder, branch)
        prev_ts_str = row["in_story_timestamp"] if row else None

        if not prev_ts_str:
            logger.debug("No previous timestamp for %s/%s, skipping", rp_folder, branch)
            return TimeAdvanceResponse()

        parsed = self.parse_timestamp(prev_ts_str)
        if not parsed:
            logger.warning("Could not parse timestamp: %s", prev_ts_str)
            return TimeAdvanceResponse(previous_timestamp=prev_ts_str)

        if override_minutes is not None:
            elapsed = override_minutes
            activities_detected: list[str] = []
            modifier_name = None
        else:
            activities = self.detect_activities(response_text)
            activities_detected = [a["name"] for a in activities]
            modifier_name, modifier_mult = self._detect_modifier(response_text)
            elapsed = self.calculate_duration(activities, modifier_mult)

        new_ts = self._advance(parsed, elapsed)
        new_ts_str = self._format(new_ts)

        # Update scene context
        if self.state_manager:
            await self.state_manager.update_scene(
                SceneUpdate(in_story_timestamp=new_ts_str),
                rp_folder,
                branch,
            )

        return TimeAdvanceResponse(
            previous_timestamp=prev_ts_str,
            new_timestamp=new_ts_str,
            elapsed_minutes=elapsed,
            activities_detected=activities_detected,
            modifier_used=modifier_name,
        )

    @staticmethod
    def detect_activities(text: str) -> list[dict]:
        """Extract activities from response text with negation/dialogue filtering."""
        activities: list[dict] = []
        seen: set[str] = set()

        for name, pattern in ACTIVITY_PATTERNS.items():
            for match in pattern.finditer(text):
                if name in seen:
                    continue
                # Negation check
                prefix = text[max(0, match.start() - 30) : match.start()]
                if _NEGATION.search(prefix):
                    continue
                # In-dialogue check
                before = text[: match.start()]
                quote_count = before.count('"')
                if quote_count % 2 == 1:
                    continue

                seen.add(name)
                activities.append({
                    "name": name,
                    "category": _get_category(name),
                })

        # Detect conversation from dialogue quotes
        dialogue_matches = re.findall(r'"[^"]+"', text)
        if dialogue_matches:
            sentences = sum(
                max(1, len(re.findall(r"[.!?]", q))) for q in dialogue_matches
            )
            conv_type = "chat" if sentences > 12 else "talk"
            activities.append({"name": conv_type, "category": "conversation"})

        return activities

    @staticmethod
    def calculate_duration(
        activities: list[dict], modifier: float = 1.0
    ) -> int:
        """Calculate total elapsed minutes using parallel/sequential logic."""
        if not activities:
            return DEFAULT_DURATION

        by_category: dict[str, list[str]] = {}
        for a in activities:
            cat = a.get("category", "other")
            by_category.setdefault(cat, []).append(a["name"])

        # Sum durations per category
        cat_durations: dict[str, int] = {}
        for cat, names in by_category.items():
            cat_durations[cat] = sum(DURATION_TABLE.get(n, DEFAULT_DURATION) for n in names)

        # Combat is never parallel — extract and add separately
        combat_dur = cat_durations.pop("combat", 0)

        # Daily is always sequential with everything
        daily_dur = cat_durations.pop("daily", 0)

        # Remaining categories happen in parallel (use max)
        remaining = list(cat_durations.values())
        max_parallel = max(remaining) if remaining else 0

        total = max_parallel + daily_dur + combat_dur

        # Apply modifier
        total = round(total * modifier)

        return max(total, MIN_DURATION)

    @staticmethod
    def parse_timestamp(text: str) -> dict | None:
        """Parse timestamp string into components."""
        m = _TIMESTAMP_RE.search(text)
        if not m:
            return None
        return {
            "weekday": m.group(1),
            "month": m.group(2),
            "day": int(m.group(3)),
            "year": int(m.group(4)),
            "hour": int(m.group(5)),
            "minute": int(m.group(6)),
            "period": m.group(7),
            "location": m.group(8).strip() if m.group(8) else None,
        }

    @staticmethod
    def _advance(ts: dict, minutes: int) -> dict:
        """Advance a parsed timestamp by N minutes, handling month/year boundaries."""
        hour24 = ts["hour"]
        if ts["period"] == "PM" and ts["hour"] != 12:
            hour24 += 12
        if ts["period"] == "AM" and ts["hour"] == 12:
            hour24 = 0

        total_min = hour24 * 60 + ts["minute"] + minutes

        days_add = 0
        while total_min >= 1440:
            total_min -= 1440
            days_add += 1
        while total_min < 0:
            total_min += 1440
            days_add -= 1

        new_h24 = total_min // 60
        new_min = total_min % 60

        # Convert to 12-hour
        new_h12 = new_h24
        new_period = "AM"
        if new_h24 >= 12:
            new_period = "PM"
            if new_h24 > 12:
                new_h12 = new_h24 - 12
        if new_h24 == 0:
            new_h12 = 12

        # Weekday rotation
        current_idx = WEEKDAYS.index(ts["weekday"]) if ts["weekday"] in WEEKDAYS else 0
        new_weekday_idx = (current_idx + days_add) % 7

        # Month/year overflow handling
        month_name = ts["month"]
        month_idx = MONTH_NAMES.get(month_name.lower(), 1) - 1  # 0-based
        day = ts["day"] + days_add
        year = ts["year"]

        # Forward overflow
        while day > DAYS_IN_MONTH[month_idx]:
            day -= DAYS_IN_MONTH[month_idx]
            month_idx += 1
            if month_idx >= 12:
                month_idx = 0
                year += 1

        # Backward overflow
        while day < 1:
            month_idx -= 1
            if month_idx < 0:
                month_idx = 11
                year -= 1
            day += DAYS_IN_MONTH[month_idx]

        return {
            "weekday": WEEKDAYS[new_weekday_idx],
            "month": MONTH_LIST[month_idx],
            "day": day,
            "year": year,
            "hour": new_h12,
            "minute": new_min,
            "period": new_period,
            "location": ts.get("location"),
        }

    @staticmethod
    def _format(ts: dict) -> str:
        """Format timestamp dict to string."""
        loc_str = f", {ts['location']}" if ts.get("location") else ""
        return (
            f"[{ts['weekday']}, {ts['month']} {ts['day']}, {ts['year']} "
            f"- {ts['hour']}:{ts['minute']:02d} {ts['period']}{loc_str}]"
        )

    @staticmethod
    def _detect_modifier(text: str) -> tuple[str | None, float]:
        """Detect the most extreme modifier in text."""
        dominant_name = None
        dominant_mult = 1.0
        dominant_dist = 0.0

        for mod in MODIFIERS:
            if mod.pattern.search(text):
                dist = abs(mod.multiplier - 1.0)
                if dist > dominant_dist:
                    dominant_dist = dist
                    dominant_mult = mod.multiplier
                    dominant_name = mod.name

        return dominant_name, dominant_mult


# Pre-computed reverse map for O(1) category lookup
_ACTIVITY_TO_CATEGORY: dict[str, str] = {
    act: cat for cat, acts in ACTIVITY_CATEGORIES.items() for act in acts
}


def _get_category(activity: str) -> str:
    """Get the category for an activity name."""
    return _ACTIVITY_TO_CATEGORY.get(activity, "other")
