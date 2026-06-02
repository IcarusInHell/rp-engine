"""Base pattern database — shared schema and CRUD for all intelligence packages.

Subclasses override:
- _category_enum: the Enum class for pattern categories
- _serialize_signature(): convert domain signature to dict for log_output
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from abc import abstractmethod
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Optional

from .types import CorrectionPair, Direction, FeedbackInput, Pattern


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class BasePatternDB:
    """Shared pattern database with identical schema across domains.

    Subclasses must set `_category_enum` to their domain's category Enum
    and implement `_serialize_signature()`.
    """

    _category_enum: type[Enum]  # Set by subclass (BehavioralCategory or PatternCategory)

    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS patterns (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                subcategory TEXT,
                description TEXT NOT NULL,
                direction TEXT CHECK(direction IN ('avoid', 'prefer')) NOT NULL,
                severity REAL DEFAULT 0.5,
                frequency INTEGER DEFAULT 0,
                correction_count INTEGER DEFAULT 0,
                proficiency REAL DEFAULT 0.2,
                context_triggers TEXT,
                compressed_rule TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_triggered DATETIME,
                last_corrected DATETIME
            );

            CREATE TABLE IF NOT EXISTS correction_pairs (
                id TEXT PRIMARY KEY,
                pattern_id TEXT NOT NULL,
                original TEXT NOT NULL,
                revised TEXT NOT NULL,
                critique TEXT,
                extracted_rule TEXT,
                tokens_original INTEGER,
                tokens_revised INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (pattern_id) REFERENCES patterns(id)
            );

            CREATE TABLE IF NOT EXISTS feedback_log (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                exchange_number INTEGER,
                original_output TEXT NOT NULL,
                user_feedback TEXT,
                user_rewrite TEXT,
                patterns_extracted TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS output_log (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                task_signature TEXT,
                patterns_injected TEXT,
                output_text TEXT NOT NULL,
                user_accepted BOOLEAN,
                corrections_made TEXT,
                monitoring_flags TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                ended_at DATETIME,
                exchanges INTEGER DEFAULT 0,
                patterns_active INTEGER DEFAULT 0,
                avg_proficiency REAL
            );
        """)
        self.conn.commit()

    # --- Pattern CRUD ---

    def insert_pattern(self, pattern: Pattern) -> str:
        """Insert a pattern row; returns its id."""
        self.conn.execute(
            """INSERT INTO patterns
               (id, category, subcategory, description, direction, severity,
                frequency, correction_count, proficiency, context_triggers,
                compressed_rule, created_at, last_triggered, last_corrected)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pattern.id,
                pattern.category.value if isinstance(pattern.category, Enum) else pattern.category,
                pattern.subcategory,
                pattern.description,
                pattern.direction.value if isinstance(pattern.direction, Direction) else pattern.direction,
                pattern.severity,
                pattern.frequency,
                pattern.correction_count,
                pattern.proficiency,
                json.dumps(pattern.context_triggers),
                pattern.compressed_rule,
                pattern.created_at.isoformat() if pattern.created_at else None,
                pattern.last_triggered.isoformat() if pattern.last_triggered else None,
                pattern.last_corrected.isoformat() if pattern.last_corrected else None,
            ),
        )
        self.conn.commit()
        return pattern.id

    def get_pattern(self, pattern_id: str) -> Optional[Pattern]:
        """Fetch a pattern by id (with its correction pairs), or None."""
        row = self.conn.execute(
            "SELECT * FROM patterns WHERE id = ?", (pattern_id,)
        ).fetchone()
        if row is None:
            return None
        pattern = self._row_to_pattern(row)
        pattern.correction_pairs = self.get_correction_pairs(pattern_id)
        return pattern

    def update_pattern(self, pattern: Pattern) -> None:
        """Persist mutable pattern fields (severity/frequency/correction count/proficiency/triggers/rule/timestamps)."""
        self.conn.execute(
            """UPDATE patterns SET
               severity = ?, frequency = ?, correction_count = ?,
               proficiency = ?, context_triggers = ?, compressed_rule = ?,
               last_triggered = ?, last_corrected = ?
               WHERE id = ?""",
            (
                pattern.severity,
                pattern.frequency,
                pattern.correction_count,
                pattern.proficiency,
                json.dumps(pattern.context_triggers),
                pattern.compressed_rule,
                pattern.last_triggered.isoformat() if pattern.last_triggered else None,
                pattern.last_corrected.isoformat() if pattern.last_corrected else None,
                pattern.id,
            ),
        )
        self.conn.commit()

    def get_all_patterns(self) -> list[Pattern]:
        """Return every pattern row (without correction pairs)."""
        rows = self.conn.execute("SELECT * FROM patterns").fetchall()
        return [self._row_to_pattern(row) for row in rows]

    def get_patterns_by_triggers(self, trigger_values: set[str]) -> list[Pattern]:
        """Return patterns whose context_triggers intersect the given trigger-value set (with correction pairs)."""
        rows = self.conn.execute("SELECT * FROM patterns").fetchall()
        matched = []
        for row in rows:
            triggers_json = row["context_triggers"]
            if not triggers_json:
                continue
            pattern_triggers = set(json.loads(triggers_json))
            if pattern_triggers & trigger_values:
                pattern = self._row_to_pattern(row)
                pattern.correction_pairs = self.get_correction_pairs(pattern.id)
                matched.append(pattern)
        return matched

    def find_pattern_by_category_subcategory(
        self, category: str, subcategory: str
    ) -> Optional[Pattern]:
        """Find a single pattern by exact (category, subcategory), or None — used for match-or-create."""
        row = self.conn.execute(
            "SELECT * FROM patterns WHERE category = ? AND subcategory = ?",
            (category, subcategory),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_pattern(row)

    # --- Correction Pairs ---

    def insert_correction_pair(self, pair: CorrectionPair) -> str:
        """Insert a correction pair (before/after example) for a pattern; returns its id."""
        self.conn.execute(
            """INSERT INTO correction_pairs
               (id, pattern_id, original, revised, critique, extracted_rule,
                tokens_original, tokens_revised, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                pair.id,
                pair.pattern_id,
                pair.original,
                pair.revised,
                pair.critique,
                pair.extracted_rule,
                pair.tokens_original,
                pair.tokens_revised,
                pair.created_at.isoformat() if pair.created_at else None,
            ),
        )
        self.conn.commit()
        return pair.id

    def get_correction_pairs(self, pattern_id: str) -> list[CorrectionPair]:
        """Return all correction pairs for a pattern."""
        rows = self.conn.execute(
            "SELECT * FROM correction_pairs WHERE pattern_id = ?",
            (pattern_id,),
        ).fetchall()
        return [self._row_to_correction_pair(row) for row in rows]

    # --- Feedback Log ---

    def log_feedback(
        self, feedback: FeedbackInput, pattern_ids: list[str]
    ) -> str:
        """Record a raw feedback submission to feedback_log; returns the log id."""
        feedback_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO feedback_log
               (id, session_id, original_output, user_feedback, user_rewrite,
                patterns_extracted, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                feedback_id,
                feedback.session_id,
                feedback.original_output,
                feedback.user_feedback,
                feedback.user_rewrite,
                json.dumps(pattern_ids),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()
        return feedback_id

    # --- Output Log ---

    @abstractmethod
    def _serialize_signature(self, signature: Any) -> dict:
        """Convert a domain-specific signature to a JSON-serializable dict."""
        ...

    def log_output(
        self, session_id: str, signature: Any,
        pattern_ids: list[str], output_text: str
    ) -> str:
        """Record an injected output (serialized signature + injected pattern ids) to output_log; returns the output id."""
        output_id = str(uuid.uuid4())
        sig_dict = self._serialize_signature(signature)
        self.conn.execute(
            """INSERT INTO output_log
               (id, session_id, task_signature, patterns_injected, output_text,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                output_id,
                session_id,
                json.dumps(sig_dict),
                json.dumps(pattern_ids),
                output_text,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()
        return output_id

    def mark_output_accepted(self, output_id: str) -> None:
        """Mark a logged output as accepted by the user."""
        self.conn.execute(
            "UPDATE output_log SET user_accepted = 1 WHERE id = ?",
            (output_id,),
        )
        self.conn.commit()

    def mark_output_corrected(
        self, output_id: str, corrected_pattern_ids: list[str]
    ) -> None:
        """Mark a logged output as corrected, recording which pattern ids were corrected."""
        self.conn.execute(
            """UPDATE output_log SET user_accepted = 0,
               corrections_made = ? WHERE id = ?""",
            (json.dumps(corrected_pattern_ids), output_id),
        )
        self.conn.commit()

    # --- Sessions ---

    def create_session(self) -> str:
        """Open a new pattern-learning session row; returns its id."""
        session_id = str(uuid.uuid4())
        self.conn.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, datetime.now(UTC).isoformat()),
        )
        self.conn.commit()
        return session_id

    def end_session(
        self, session_id: str, exchanges: int,
        patterns_active: int, avg_proficiency: float
    ) -> None:
        """Close a session, recording exchange count, active-pattern count, and average proficiency."""
        self.conn.execute(
            """UPDATE sessions SET ended_at = ?, exchanges = ?,
               patterns_active = ?, avg_proficiency = ? WHERE id = ?""",
            (
                datetime.now(UTC).isoformat(),
                exchanges,
                patterns_active,
                avg_proficiency,
                session_id,
            ),
        )
        self.conn.commit()

    # --- Utility ---

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self.conn.close()

    def _row_to_pattern(self, row: sqlite3.Row) -> Pattern:
        triggers_json = row["context_triggers"]
        triggers = json.loads(triggers_json) if triggers_json else []
        return Pattern(
            id=row["id"],
            category=self._category_enum(row["category"]),
            subcategory=row["subcategory"],
            description=row["description"],
            direction=Direction(row["direction"]),
            severity=row["severity"],
            frequency=row["frequency"],
            correction_count=row["correction_count"],
            proficiency=row["proficiency"],
            context_triggers=triggers,
            compressed_rule=row["compressed_rule"],
            created_at=_parse_dt(row["created_at"]),
            last_triggered=_parse_dt(row["last_triggered"]),
            last_corrected=_parse_dt(row["last_corrected"]),
        )

    def _row_to_correction_pair(self, row: sqlite3.Row) -> CorrectionPair:
        return CorrectionPair(
            id=row["id"],
            pattern_id=row["pattern_id"],
            original=row["original"],
            revised=row["revised"],
            critique=row["critique"],
            extracted_rule=row["extracted_rule"],
            tokens_original=row["tokens_original"] or 0,
            tokens_revised=row["tokens_revised"] or 0,
            created_at=_parse_dt(row["created_at"]),
        )
