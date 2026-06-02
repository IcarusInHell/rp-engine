"""Bounded LRU cache for per-key in-memory caching.

Used by services that cache expensive-to-compute data keyed by rp_folder,
branch, file path, etc. Prevents unbounded memory growth on long-running
servers with many RP folders or branches.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

KT = TypeVar("KT")
VT = TypeVar("VT")


class LRUCache(Generic[KT, VT]):  # noqa: UP046
    """Bounded LRU cache backed by OrderedDict.

    On cache hit, entries are promoted to most-recently-used.
    When the cache exceeds ``maxsize``, the least-recently-used entry is evicted.
    """

    def __init__(self, maxsize: int = 16) -> None:
        self._data: OrderedDict[KT, VT] = OrderedDict()
        self.maxsize = maxsize

    def get(self, key: KT) -> VT | None:
        """Get value, promoting to most-recently-used. Returns None if missing."""
        if key not in self._data:
            return None
        self._data.move_to_end(key)
        return self._data[key]

    def put(self, key: KT, value: VT) -> None:
        """Insert or update value, evicting LRU entries if over maxsize."""
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def pop(self, key: KT, default: VT | None = None) -> VT | None:
        """Remove and return a specific entry."""
        return self._data.pop(key, default)

    def clear(self) -> None:
        """Remove all entries."""
        self._data.clear()

    def __contains__(self, key: KT) -> bool:
        """Check if key exists (does NOT promote)."""
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        """Return a view of cached keys in LRU→MRU order (does NOT promote)."""
        return self._data.keys()
