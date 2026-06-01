"""Route-order lock for the cards + exchanges routers.

The cards router has two *load-bearing* inline comments (cards.py:257, :570)
warning that ``/reindex``, ``/schema/{card_type}``, ``/validate`` (and the other
literal paths) MUST be registered before the catch-all ``/{card_type}/{name}``.
After the Phase 6a/7a splits that hazard moves from "function order in one file"
to "include_router order in __init__.py" — comments don't enforce it, this test
does (split-cards-router PoC-6 / split-exchanges-router verification step).

This asserts INVARIANTS (specific-before-catch-all, no duplicate routes), not an
exact snapshot — so it survives additive route changes but breaks loudly if a
literal path is ever shadowed by the catch-all. Phases 6a and 7a legitimately
update these expectations (the cards package split; the exchanges split + the two
dead-endpoint removals); those phases own the edit.
"""

from __future__ import annotations

from fastapi.routing import APIRoute

from rp_engine.main import app


def _routes() -> list[tuple[frozenset[str], str]]:
    """(methods, path) for every APIRoute, in registration order."""
    out: list[tuple[frozenset[str], str]] = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            out.append((frozenset(r.methods or set()), r.path))
    return out


def _paths_in_order() -> list[str]:
    return [path for _, path in _routes()]


def test_no_duplicate_method_path_pairs():
    """A split that double-mounts a router shows up as a duplicate (method, path)."""
    routes = _routes()
    seen: set[tuple[frozenset[str], str]] = set()
    dupes = [r for r in routes if r in seen or seen.add(r)]
    assert not dupes, f"duplicate routes registered (double-mount?): {dupes}"


def test_cards_catch_all_registered_after_literal_paths():
    """The ``/{card_type}/{name}`` catch-all must come AFTER every literal path.

    If a literal (``/reindex``, ``/suggest``, ...) ever lands after the catch-all,
    FastAPI resolves e.g. ``POST /api/cards/reindex`` to ``create_card`` with
    ``card_type='reindex'`` — a silent mis-route. This is the enforcement the
    load-bearing comments asked for.
    """
    paths = _paths_in_order()
    catch_all_idx = paths.index("/api/cards/{card_type}/{name}")

    literal_before = [
        "/api/cards/reindex",
        "/api/cards/suggest",
        "/api/cards/audit",
        "/api/cards/connections",
        "/api/cards/validate",
        "/api/cards/generate-name",
        "/api/cards/schema/{card_type}",
        "/api/cards/gaps/{entity_name}/evidence",
    ]
    for literal in literal_before:
        assert literal in paths, f"expected route {literal!r} is missing"
        assert paths.index(literal) < catch_all_idx, (
            f"ROUTE SHADOWED: {literal!r} is registered AFTER the catch-all "
            f"/api/cards/{{card_type}}/{{name}} — it will never match."
        )


def test_cards_post_create_after_post_literals():
    """The catch-all POST ``/{card_type}`` must come after the literal POSTs."""
    routes = _routes()
    post_paths = [path for methods, path in routes if "POST" in methods]
    create_idx = post_paths.index("/api/cards/{card_type}")
    for literal in ("/api/cards/reindex", "/api/cards/suggest", "/api/cards/audit"):
        assert post_paths.index(literal) < create_idx, (
            f"ROUTE SHADOWED: POST {literal!r} after catch-all POST /api/cards/{{card_type}}"
        )


def test_exchanges_resource_roots_all_mounted():
    """All three exchange-domain URL roots are present (the split must preserve them).

    Phase 7a moved bookmarks/annotations into their own files (``bookmarks.py`` /
    ``annotations.py``); these roots must survive the move intact.
    """
    paths = set(_paths_in_order())
    expected = {
        "/api/exchanges",
        "/api/exchanges/{exchange_number}",
        "/api/exchanges/search",
        "/api/exchanges/{exchange_number}/bookmark",
        "/api/bookmarks",
        "/api/exchanges/{exchange_number}/annotations",
        "/api/annotations/{annotation_id}",
        "/api/annotations",
    }
    missing = expected - paths
    assert not missing, f"expected exchange-domain routes missing: {missing}"


def test_dead_exchange_endpoints_removed():
    """Phase 7a removed two dead (zero-caller) endpoints — lock their absence.

    ``GET /api/exchanges/recent`` (unscoped across all RPs) and
    ``DELETE /api/exchanges/{exchange_id}`` (deprecated legacy delete) are gone.
    Asserting absence — not merely dropping them from the present-set — keeps the
    removal locked against an accidental re-add.
    """
    paths = set(_paths_in_order())
    assert "/api/exchanges/recent" not in paths, (
        "GET /api/exchanges/recent should have been removed in Phase 7a"
    )
    assert "/api/exchanges/{exchange_id}" not in paths, (
        "DELETE /api/exchanges/{exchange_id} should have been removed in Phase 7a"
    )
