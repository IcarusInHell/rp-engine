"""Smoke test for the container + endpoint harness.

Phase 0a builds ``create_test_app`` / ``built_container`` but deliberately
exercises them with a *single* smoke test — full endpoint coverage lives in
Phase 0c. This proves three things end to end:

1. The real ServiceContainer builds against a temp vault with the fake LLM.
2. Config priming reaches runtime ``get_config()`` consumers (not just the
   passed config object).
3. A live endpoint responds, having indexed the sample cards.
"""

from __future__ import annotations

import rp_engine.config as rp_config

from tests.conftest import DEFAULT_POV, RP_FOLDER


def test_primed_config_reaches_get_config(primed_config):
    """A runtime ``get_config()`` call (the shared lru_cache) sees the test value."""
    assert rp_config.get_config().rp.default_pov_character == DEFAULT_POV
    assert primed_config.rp.default_pov_character == DEFAULT_POV


async def test_container_builds_and_indexes_sample_vault(built_container):
    indexed = await built_container.db.fetch_val("SELECT COUNT(*) FROM story_cards")
    # 2 characters + 1 location + 1 lore = 4 cards from the sample vault.
    assert indexed == 4, f"expected 4 indexed cards, got {indexed}"

    # Card-seeded directional trust baselines landed on main.
    ab = await built_container.db.fetch_val(
        """SELECT baseline_score FROM trust_baselines
           WHERE rp_folder = ? AND branch = 'main'
             AND character_a = 'Alice' AND character_b = 'Bob'""",
        [RP_FOLDER],
    )
    assert ab == 5


async def test_health_endpoint_responds(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["indexed_cards"] == 4
    assert body["database"]["journal_mode"].lower() == "wal"
