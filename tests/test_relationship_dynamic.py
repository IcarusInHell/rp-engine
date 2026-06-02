"""Service: relationship-role (``dynamic``) resolution across entity_connections readers.

Silent-drop guard for the relationship-role-lookup bug *class*. ``entity_connections``
stores both endpoints as ``"rp_folder:normalize_key(name)"``, but several lookup
paths historically compared *bare display names* against those ``folder:key`` rows
— a guaranteed miss, so the relationship role resolved to ``None`` every time, and
nothing asserted on it so the drop was invisible. Three independent readers carry
this risk:

- ``RelationshipService._get_relationship`` (per-pair SQL)
- ``RelationshipService.get_all_relationships`` (batch role_map)
- ``NPCEngine._load_trust_relationship`` (per-reaction role, injected into the NPC
  prompt as ``- Dynamic: …``) — fixed last; the first two "RESOLVED" passes missed it.

Each is an independent codepath with its own failure mode, so each gets its own
test (revert one fix → only its test goes red). The RP folder name is deliberately
MixedCase (``DynRP``): ``get_all_relationships`` lowercases the whole stored key
including the folder prefix, so a fix that prefixes the lookup key without also
lowercasing the folder part still misses — only a lowercase folder would hide that,
which this fixture refuses to do.
"""

from __future__ import annotations

import pytest

# MixedCase on purpose — see module docstring (Path-2 lowering guard).
DYN_RP = "DynRP"
ROLE = "confidante"


def _write_role_vault(vault_root) -> None:
    """A two-character RP where Alice declares a roled relationship toward Bob.

    ``npc_trust_levels`` seeds the directional trust baselines (so both lookup
    paths actually return a row to attach ``dynamic`` to); ``relationships``
    seeds the ``has_relationship`` entity_connections row that carries the role.
    """
    chars = vault_root / DYN_RP / "Story Cards" / "Characters"
    chars.mkdir(parents=True)

    (chars / "alice.md").write_text(
        "---\n"
        "type: character\n"
        "name: Alice\n"
        "npc_trust_levels:\n"
        "  Bob: 5\n"
        "relationships:\n"
        "  - target: Bob\n"
        f"    role: {ROLE}\n"
        "---\n"
        "Alice keeps Bob closer than she admits.\n",
        encoding="utf-8",
    )
    (chars / "bob.md").write_text(
        "---\n"
        "type: character\n"
        "name: Bob\n"
        "npc_trust_levels:\n"
        "  Alice: 7\n"
        "---\n"
        "Bob trusts Alice without quite knowing why.\n",
        encoding="utf-8",
    )


@pytest.fixture
async def role_rp(built_container, primed_config):
    """Index the role vault into ``DynRP`` and hand back the live container."""
    from pathlib import Path

    _write_role_vault(Path(primed_config.paths.vault_root))
    await built_container.card_indexer.full_index(DYN_RP)
    return built_container


async def test_get_all_relationships_resolves_dynamic(role_rp):
    """Path 2: the batch lookup must attach the role to the Alice→Bob row.

    Reverting the role_map key fix (or dropping the ``.lower()`` on the built
    key) makes ``dynamic`` None again → this assertion fails loud.
    """
    rels = await role_rp.state_manager.get_all_relationships(DYN_RP, "main")

    ab = next((r for r in rels if r.character_a == "Alice" and r.character_b == "Bob"), None)
    assert ab is not None, "SILENT DROP: Alice→Bob relationship row missing entirely"
    assert ab.dynamic == ROLE, (
        f"SILENT DROP: relationship role not resolved — dynamic={ab.dynamic!r}, "
        f"expected {ROLE!r}. The lookup key did not match the stored "
        f"'rp_folder:key' entity_connections endpoint."
    )


async def test_get_relationship_resolves_dynamic(role_rp):
    """Path 1: the per-pair SQL read must attach the role too.

    Reverting the ``make_entity_id`` keying in ``_get_relationship`` leaves the
    SQL comparing bare names against 'folder:key' rows → dynamic None → red.
    """
    rel = await role_rp.state_manager.relationships._get_relationship(
        "Alice", "Bob", DYN_RP, "main"
    )

    assert rel is not None, "SILENT DROP: Alice→Bob relationship resolved to None"
    assert rel.dynamic == ROLE, (
        f"SILENT DROP: _get_relationship did not resolve the role — "
        f"dynamic={rel.dynamic!r}, expected {ROLE!r}."
    )


async def test_npc_engine_load_trust_relationship_resolves_dynamic(role_rp):
    """Path 3: the NPC reaction loader must attach the role too (npc→pov).

    ``NPCEngine._load_trust_relationship`` feeds ``dynamic`` into the NPC prompt
    (``- Dynamic: {role}``). The role lookup is bidirectional, so an NPC reacting
    toward the POV resolves the Alice↔Bob role regardless of direction. Reverting
    the ``make_entity_id`` keying leaves the SQL comparing bare names against
    'folder:key' rows → dynamic None → red.
    """
    trust = await role_rp.npc_engine._load_trust_relationship(
        "Bob", "Alice", DYN_RP, "main"
    )

    assert trust.dynamic == ROLE, (
        f"SILENT DROP: NPC reaction role not resolved — dynamic={trust.dynamic!r}, "
        f"expected {ROLE!r}. The lookup key did not match the stored "
        f"'rp_folder:key' entity_connections endpoint."
    )
