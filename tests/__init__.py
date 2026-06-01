"""RP Engine test suite (rewritten fresh — see bug-fixes-roadmap Phase 0).

House style: silent-drop-aware assertions. This codebase's signature failure
mode is *silently dropped data* — empty results, conflated directions,
miscounts, and ``except: return ""`` swallow-points. Tests must fail LOUD at
those points (presence/non-empty, directional inequality, exact counts), not
merely check shape. See ``tests/assertions.py`` for the shared vocabulary.
"""
