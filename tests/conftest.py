"""Shared test harness — fixtures and the deterministic LLM stub.

Two injection styles are supported, per the roadmap:

1. **Direct service fixtures** (``db``, ``resolver``, ``branch_manager``) — for
   foundation/service unit tests. No container, no LLM, no get_config() path.
2. **Container + endpoint fixtures** (``built_container``, ``client``) — build the
   real ``ServiceContainer`` with a fake LLM provider (patched *below* LLMClient
   so VectorSearch/LanceStore closures stay intact), then drive endpoints via
   httpx ASGITransport.

Config control: there is no root ``config.yaml``, so ``_load_yaml_defaults()``
returns ``{}`` and env vars take precedence. ``primed_config`` sets
``RP_ENGINE_*`` env and clears the ``get_config`` lru_cache — this reaches every
``from rp_engine.config import get_config`` consumer because they all share the
one cached function object.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import pytest
import pytest_asyncio

import rp_engine.config as rp_config
from rp_engine.database import Database
from rp_engine.services.llm._types import LLMResponse

EMBED_DIM = 1536
RP_FOLDER = "TestRP"
DEFAULT_POV = "TestPC"


# ---------------------------------------------------------------------------
# Deterministic LLM stub (a provider, injected below LLMClient)
# ---------------------------------------------------------------------------

def deterministic_vector(text: str, dim: int = EMBED_DIM) -> list[float]:
    """A stable, non-zero embedding derived from the text.

    Non-zero matters: ``container.build`` only treats vectors as real (and skips
    a re-index) when ``has_real_embedding`` sees a non-zero blob.
    """
    vals: list[float] = []
    counter = 0
    while len(vals) < dim:
        block = hashlib.sha256(f"{text}:{counter}".encode()).digest()
        for i in range(0, len(block), 4):
            if len(vals) >= dim:
                break
            n = struct.unpack("<I", block[i : i + 4])[0]
            vals.append((n / 2**32) - 0.5)
        counter += 1
    return vals


class FakeProvider:
    """In-memory ``LLMProvider`` stub: canned generations, deterministic embeds.

    Records calls so tests can assert what the prompt assembler actually sent
    (a silent drop shows up as a card/exchange missing from ``generate_calls``).
    """

    def __init__(self, dimension: int = EMBED_DIM) -> None:
        self.dimension = dimension
        self.generate_calls: list[dict] = []
        self.embed_calls: list[list[str]] = []
        self._queued: list[str] = []
        self.default_content = "She studies you for a long moment before answering."

    def queue(self, *contents: str) -> None:
        """Queue canned ``generate`` responses, consumed FIFO."""
        self._queued.extend(contents)

    def _next(self) -> str:
        return self._queued.pop(0) if self._queued else self.default_content

    async def generate(
        self, messages, model, temperature=0.6, max_tokens=1500, response_format=None
    ) -> LLMResponse:
        self.generate_calls.append(
            {"messages": messages, "model": model, "response_format": response_format}
        )
        return LLMResponse(
            content=self._next(),
            model=model,
            usage={"prompt_tokens": 16, "completion_tokens": 32},
        )

    async def generate_stream(self, messages, model, temperature=0.6, max_tokens=1500):
        self.generate_calls.append({"messages": messages, "model": model, "stream": True})
        for token in self._next().split(" "):
            yield token + " "

    async def embed(self, texts, model) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [deterministic_vector(t, self.dimension) for t in texts]

    async def close(self) -> None:  # noqa: D401 - protocol method
        pass


# ---------------------------------------------------------------------------
# Direct service fixtures (no container)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db(tmp_path) -> Database:
    """A fresh, migrated, file-backed Database. Closed on teardown."""
    database = Database(tmp_path / "test.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture
async def resolver(db):
    from rp_engine.services.ancestry_resolver import AncestryResolver

    return AncestryResolver(db)


@pytest_asyncio.fixture
async def branch_manager(db, resolver):
    from rp_engine.services.branch_manager import BranchManager
    from rp_engine.services.state_manager import StateManager

    state_manager = StateManager(db=db, resolver=resolver)
    return BranchManager(db=db, state_manager=state_manager, resolver=resolver)


# ---------------------------------------------------------------------------
# Config priming + container/endpoint fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def primed_config(tmp_path, monkeypatch):
    """Point ``get_config()`` at an isolated temp vault + db.

    Reaches all ``from rp_engine.config import get_config`` consumers via the
    shared lru_cache (cleared here, rebuilt from env). Absolute temp paths
    survive ``resolve_paths()`` (joining PROJECT_ROOT with an absolute path
    yields the absolute path).
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    db_path = tmp_path / "data" / "rp-engine.db"

    monkeypatch.setenv("RP_ENGINE_PATHS__VAULT_ROOT", str(vault))
    monkeypatch.setenv("RP_ENGINE_PATHS__DB_PATH", str(db_path))
    monkeypatch.setenv("RP_ENGINE_RP__DEFAULT_POV_CHARACTER", DEFAULT_POV)
    # Keep background/optional subsystems off for deterministic tests.
    monkeypatch.setenv("RP_ENGINE_AUTO_SAVE__ENABLED", "false")
    monkeypatch.setenv("RP_ENGINE_CONTINUITY__ENABLED", "false")
    monkeypatch.setenv("RP_ENGINE_DIAGNOSTICS__ENABLED", "false")

    rp_config.get_config.cache_clear()
    cfg = rp_config.get_config()
    try:
        yield cfg
    finally:
        rp_config.get_config.cache_clear()


def _write_sample_vault(vault: Path) -> None:
    """Write a minimal but realistic RP: two characters (directional trust),
    a location, and a lore card — enough for full_index + reindex to do work.
    """
    cards = vault / RP_FOLDER / "Story Cards"
    chars = cards / "Characters"
    chars.mkdir(parents=True)
    (cards / "Locations").mkdir()
    (cards / "Lore").mkdir()

    (chars / "alice.md").write_text(
        "---\n"
        "type: character\n"
        "name: Alice\n"
        "importance: high\n"
        "npc_trust_levels:\n"
        "  Bob: 5\n"
        "---\n"
        "Alice is a wary tavern keeper who has seen too much.\n",
        encoding="utf-8",
    )
    (chars / "bob.md").write_text(
        "---\n"
        "type: character\n"
        "name: Bob\n"
        "importance: medium\n"
        "npc_trust_levels:\n"
        "  Alice: 10\n"
        "---\n"
        "Bob is a travelling merchant who trusts Alice deeply.\n",
        encoding="utf-8",
    )
    (cards / "Locations" / "tavern.md").write_text(
        "---\ntype: location\nname: The Salt Tavern\n---\n"
        "A dim harbourside tavern that smells of brine and woodsmoke.\n",
        encoding="utf-8",
    )
    (cards / "Lore" / "the-pact.md").write_text(
        "---\ntype: lore\nname: The Old Pact\n---\n"
        "An ancient agreement binding the coastal towns.\n",
        encoding="utf-8",
    )


@pytest_asyncio.fixture
async def built_container(primed_config, monkeypatch):
    """A fully built ServiceContainer wired to a fake LLM provider.

    The fake is injected by patching ``rp_engine.container.build_providers`` so
    it lands *below* LLMClient — keeping the ``llm_client.embed`` closures that
    VectorSearch and LanceStore captured at construction intact.
    """
    vault = Path(primed_config.paths.vault_root)
    _write_sample_vault(vault)

    fake = FakeProvider(dimension=primed_config.search.embedding_dimension)
    monkeypatch.setattr(
        "rp_engine.container.build_providers",
        lambda config: {config.llm.provider: fake},
    )

    from rp_engine.container import ServiceContainer

    container = await ServiceContainer.build(primed_config)
    container.fake_provider = fake  # test handle (dynamic attr on the dataclass)
    try:
        yield container
    finally:
        await container.close()


class SeededRP:
    """Handle for the shared, fully-populated RP used by service-layer tests.

    Bundles the live container with the seeding facts a test needs to assert on
    (branch names, exchange counts) so NPC-ancestry / context / analysis tests
    share one populated RP instead of wiring it ad hoc (roadmap Phase 0b step 0).
    """

    def __init__(self, container, *, rp_folder, main_branch, child_branch, n_main_exchanges):
        self.container = container
        self.rp_folder = rp_folder
        self.main_branch = main_branch
        self.child_branch = child_branch
        self.n_main_exchanges = n_main_exchanges

    @property
    def db(self):
        return self.container.db


@pytest_asyncio.fixture
async def seeded_rp(built_container) -> SeededRP:
    """A populated RP on top of ``built_container``: the sample vault (already
    indexed, with directional trust seeded) PLUS a session, N exchanges on
    ``main``, and a fresh child branch ``B`` that owns no exchanges of its own.

    The child branch is the lever for the silent-drop tests: it must *see* its
    parent's N exchanges through ancestry, even though a direct branch-scoped
    query finds zero.
    """
    from tests import factories

    container = built_container
    n = 3

    await container.branch_manager.ensure_main_branch(RP_FOLDER)
    await factories.insert_session(container.db)
    for i in range(1, n + 1):
        await factories.insert_exchange(
            container.db,
            i,
            user_message=f"Alice and Bob talk at the tavern (turn {i})",
            assistant_response=f"Alice studies Bob across the bar (turn {i}).",
        )
    await container.branch_manager.create_branch("B", RP_FOLDER, branch_from="main")

    return SeededRP(
        container,
        rp_folder=RP_FOLDER,
        main_branch="main",
        child_branch="B",
        n_main_exchanges=n,
    )


@pytest_asyncio.fixture
async def client(built_container):
    """An httpx AsyncClient over the real app with the test container attached.

    ``main.app`` is a module global, so app.state is shared — every attribute we
    set here is removed on teardown to prevent cross-test contamination.
    """
    import httpx

    from rp_engine.container import ServiceContainer
    from rp_engine.main import app

    fields = list(ServiceContainer.__dataclass_fields__)
    touched: list[str] = []

    app.state.services = built_container
    touched.append("services")
    for field in fields:
        setattr(app.state, field, getattr(built_container, field))
        touched.append(field)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as c:
            yield c
    finally:
        app.dependency_overrides.clear()
        for attr in touched:
            if hasattr(app.state, attr):
                delattr(app.state, attr)
