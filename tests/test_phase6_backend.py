"""Phase 6 — backend gap-fills that unblock the frontend UI.

Three small wirings, each guarding a silent-drop point:
1. ``injection_depths`` now has a write path through ``PUT /api/context/guidelines``
   (it was read at prompt assembly since Phase 4 but no endpoint accepted it).
2. global ``prompt.*`` config (token_budget / example_dialogue) is now writable
   via ``PUT /api/config`` and echoed by ``GET /api/config``.
3. ``GET /api/context/guidelines/system-prompt`` surfaces the effective
   per-section injection depths so the preview can show section positions.
"""

from __future__ import annotations

import rp_engine.routers.config as config_router
from tests.conftest import RP_FOLDER

Q = {"rp_folder": RP_FOLDER}


def _write_guidelines(seeded_rp, extra: str = "") -> None:
    vault = seeded_rp.container.prompt_assembler.vault_root
    path = vault / RP_FOLDER / "RP State" / "Story_Guidelines.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ninclude_npc_framework: true\n{extra}---\nGuidelines body.\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. injection_depths write path
# ---------------------------------------------------------------------------

async def test_injection_depths_round_trips_through_guidelines_put(client, seeded_rp):
    """Write injection_depths, then a fresh GET returns it.

    Mutation guard: dropping ``"injection_depths"`` from ``allowed_fields`` in
    ``update_guidelines`` makes the write a no-op → this assertion reddens.
    """
    _write_guidelines(seeded_rp)
    depths = {"scene_context": 4, "triggered_notes": 2}

    put = await client.put(
        "/api/context/guidelines", params=Q, json={"injection_depths": depths},
    )
    assert put.status_code == 200, put.text
    assert put.json()["injection_depths"] == depths

    got = await client.get("/api/context/guidelines", params=Q)
    assert got.status_code == 200, got.text
    assert got.json()["injection_depths"] == depths


# ---------------------------------------------------------------------------
# 2. global prompt config write path
# ---------------------------------------------------------------------------

async def test_config_get_echoes_prompt_section(client, seeded_rp):
    """GET /api/config exposes the global prompt config.

    Mutation guard: dropping ``"prompt"`` from ``_config_to_dict`` (or the
    ``ConfigResponse`` field) reddens this — the settings UI would have nothing
    to populate the token-budget / example-dialogue controls from.
    """
    got = await client.get("/api/config")
    assert got.status_code == 200, got.text
    prompt_cfg = got.json()["prompt"]
    # The PromptConfig defaults (Phase 4): example_dialogue on, token_budget off.
    assert "token_budget" in prompt_cfg
    assert "example_dialogue" in prompt_cfg


async def test_config_put_persists_prompt_section_to_yaml(
    client, seeded_rp, tmp_path, monkeypatch,
):
    """PUT /api/config writes prompt.* into config.yaml.

    Asserts against the written yaml file (not a get_config reload, which reads
    PROJECT_ROOT/config.yaml independently). Mutation guard: dropping
    ``"prompt"`` from ``update_config``'s section_map means the section is never
    written → the yaml read-back reddens.
    Isolated: ``_CONFIG_PATH`` is the real repo config.yaml, monkeypatched here.
    """
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr(config_router, "_CONFIG_PATH", cfg_path)

    put = await client.put(
        "/api/config",
        json={"prompt": {"token_budget": {"enabled": True}, "example_dialogue": {"max_examples": 5}}},
    )
    assert put.status_code == 200, put.text

    import yaml
    written = yaml.safe_load(cfg_path.read_text())
    assert written["prompt"]["token_budget"]["enabled"] is True
    assert written["prompt"]["example_dialogue"]["max_examples"] == 5


# ---------------------------------------------------------------------------
# 3. preview surfaces injection depths
# ---------------------------------------------------------------------------

async def test_system_prompt_preview_includes_injection_metadata(client, seeded_rp):
    """The static preview reports injection enabled-state + effective depths.

    Mutation guard: removing the ``"injection"`` key from the endpoint response
    reddens this test (the preview UI would lose its depth annotations).
    """
    _write_guidelines(seeded_rp)
    resp = await client.get("/api/context/guidelines/system-prompt", params=Q)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "injection" in body
    assert "enabled" in body["injection"]
    assert isinstance(body["injection"]["depths"], dict)


async def test_preview_reflects_per_rp_injection_depth_override(client, seeded_rp):
    """A per-RP injection_depths override surfaces in the preview depth map.

    Ties gap-fill #1 (write path) to gap-fill #3 (preview view): a depth set via
    the guidelines PUT is the depth the preview reports.
    """
    _write_guidelines(seeded_rp)
    await client.put(
        "/api/context/guidelines", params=Q,
        json={"injection_depths": {"scene_context": 7}},
    )
    resp = await client.get("/api/context/guidelines/system-prompt", params=Q)
    assert resp.json()["injection"]["depths"]["scene_context"] == 7
