"""A concurrency gate whose limit can change at runtime, safely.

Replaces ``asyncio.Semaphore`` for provider request gating. ``Semaphore`` has a
fixed initial count, so the original adaptive-concurrency code (git 0e90034,
``llm_client.py``) "resized" it by *replacing the object mid-flight* — which
over-admits (in-flight callers hold the old object) and read the private
``_semaphore._value``. This primitive resizes correctly:

- shrinking never evicts in-flight work — it just stops admitting until
  ``in_use`` drains below the new limit;
- growing wakes blocked waiters;
- no object is ever replaced and no private internals are read.

The caller's configured ``max_concurrency`` is the ``ceiling`` (a hard "don't go
higher than this" guard); adaptive logic only ever pulls the live limit *down*
from it via :meth:`set_limit`.
"""

from __future__ import annotations

import asyncio


class AdjustableLimit:
    """An async concurrency gate with a runtime-adjustable limit.

    Use as an async context manager::

        gate = AdjustableLimit(limit=5, ceiling=5)
        async with gate:
            ...  # at most `limit` of these run concurrently
    """

    def __init__(self, limit: int, ceiling: int) -> None:
        self._ceiling = max(1, ceiling)
        self._limit = max(1, min(limit, self._ceiling))
        self._in_use = 0
        self._cond = asyncio.Condition()

    @property
    def limit(self) -> int:
        """The current admission limit (≤ ceiling)."""
        return self._limit

    @property
    def ceiling(self) -> int:
        """The hard upper bound the limit can never exceed."""
        return self._ceiling

    @property
    def in_use(self) -> int:
        """How many permits are currently held."""
        return self._in_use

    async def __aenter__(self) -> "AdjustableLimit":
        async with self._cond:
            await self._cond.wait_for(lambda: self._in_use < self._limit)
            self._in_use += 1
        return self

    async def __aexit__(self, *exc) -> None:
        async with self._cond:
            self._in_use -= 1
            self._cond.notify_all()

    async def set_limit(self, n: int) -> None:
        """Adjust the live limit, clamped to ``[1, ceiling]``.

        Growing wakes blocked waiters; shrinking takes effect by withholding new
        admissions until ``in_use`` drains — in-flight work is never cancelled.
        """
        async with self._cond:
            self._limit = max(1, min(n, self._ceiling))
            self._cond.notify_all()
