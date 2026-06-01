"""Silent-drop-aware assertion helpers — the house vocabulary.

The point of every helper here is to convert a *silent* failure (empty string,
dropped row, conflated direction, off-by-one count) into a LOUD test failure
with a message that names what was dropped. Prefer these over bare ``assert``
whenever a drop would otherwise yield an empty/plausible-but-wrong value.
"""

from __future__ import annotations

from collections.abc import Sized
from typing import Any


def assert_present(needle: Any, haystack: Any, *, label: str = "") -> None:
    """Fail loudly if ``needle`` is absent from ``haystack``.

    ``haystack`` may be a string (substring check) or any container (membership).
    A dropped attachment/card/exchange manifests as a missing needle — this is the
    assertion that catches it.
    """
    where = f" in {label}" if label else ""
    if isinstance(haystack, str):
        assert needle in haystack, (
            f"SILENT DROP: expected {needle!r} to appear{where}, but it was absent.\n"
            f"--- haystack (len={len(haystack)}) ---\n{haystack[:1000]}"
        )
        return
    assert needle in haystack, (
        f"SILENT DROP: expected {needle!r} to be present{where}, but it was absent.\n"
        f"--- haystack ---\n{haystack!r}"
    )


def assert_nonempty(value: Sized | None, *, label: str = "value") -> None:
    """Fail loudly if ``value`` is None or empty.

    An empty list/string is the classic signature of a swallowed result
    (``except: return ""`` / ``if not x: return []``).
    """
    assert value is not None, f"SILENT DROP: {label} is None (expected non-empty)."
    assert len(value) > 0, f"SILENT DROP: {label} is empty (len 0) — expected non-empty."


def assert_count_exact(actual: int, expected: int, *, label: str = "count") -> None:
    """Fail loudly unless ``actual == expected``.

    Never use ``>= 0`` / ``> 0`` for counts that have a knowable exact value —
    a miscount (e.g. memories tallied as events) hides behind a loose bound.
    """
    assert actual == expected, (
        f"COUNT MISMATCH: {label} = {actual}, expected exactly {expected}."
    )


def assert_directional(forward: Any, reverse: Any, *, label: str = "relation") -> None:
    """Fail loudly if a directional value was flattened (``a→b`` == ``b→a``).

    Use where the two directions are *seeded differently* — if they read back
    equal, a direction was conflated/OR-merged somewhere.
    """
    assert forward != reverse, (
        f"DIRECTION FLATTENED: {label} a→b == b→a == {forward!r}; "
        f"the two directions were conflated (expected them to differ)."
    )
