"""Drift guard: every table the export bundles must be wired into import.

``export_service`` declares its tables in two flat lists (``_CRITICAL_TABLES``,
``_OPTIONAL_TABLES``); ``import_service._import_state`` restores them imperatively
in FK-safe order. Nothing links the two sides, so adding a table to export and
forgetting to wire ``_import_state`` silently bundles data that import never
restores — a round-trip data loss the ``_read_json`` / ``_read_jsonl``
"``[]`` on missing file" floor swallows without any error.

Two complementary guards live here:

1. ``test_every_exported_table_is_imported`` — a cheap STATIC name-subset check:
   every exported table *name* must be referenced by ``_import_state``. Catches
   the most-likely mode (add-a-table-forget-to-import-it-entirely) with zero
   seeding, but is blind to *prefix flips* (table moved critical↔optional on one
   side only) and *format flips* (jsonl on export only): ``_import_table_batch``
   builds the archive path as an f-string, so the real ``state/x.json`` paths are
   not source-introspectable — only the bare table name is.

2. ``test_seeded_round_trip_preserves_every_table`` — the BEHAVIORAL backstop that
   the static check can't be: seed ≥1 row in every export-spec table, run a real
   export→import into a fresh target DB, and assert every table's rows survive.
   A prefix/format flip (or any import-side drop) makes that table's file land
   somewhere import doesn't read → ``[]``-floor → zero rows in the target → red.
   Its own false-green (an unseeded table passes vacuously, 0→0) is closed by a
   meta-assert that export actually bundled a file for every table first.

   CEILING: it seeds exactly ONE row per table, so it verifies path / format / FK
   *wiring*, NOT row-count fidelity and NOT multi-row drops. Import uses
   ``INSERT OR IGNORE``, which (verified) raises on FK violations but SILENTLY
   swallows NOT NULL / UNIQUE / PK collisions — a second row that collides on such
   a constraint would be dropped with no error and this test would stay green. A
   fail-loud-on-skipped-row import pass is a separate, unbuilt decision.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
import zipfile

from rp_engine.database import Database
from rp_engine.services import export_service, import_service
from rp_engine.services.export_service import export_rp
from rp_engine.services.import_service import import_rp


def _exported_table_names() -> set[str]:
    """Every table name export bundles, across the critical + optional lists."""
    specs = export_service._CRITICAL_TABLES + export_service._OPTIONAL_TABLES
    return {spec["name"] for spec in specs}


def _import_state_string_literals() -> set[str]:
    """All str-constant literals in ``_import_state``'s body, via ``ast``.

    ``ast`` (not substring matching) so that (a) a table name buried in a comment
    cannot vacuously satisfy the guard, and (b) ``"analysis_manifests"`` cannot
    false-match inside ``"analysis_manifest_entries"``. Every table import handles
    appears as a bare string literal here (the table arg to ``_import_table_batch``
    / ``_insert_row``), even where the archive *path* is an un-introspectable
    f-string.
    """
    src = textwrap.dedent(inspect.getsource(import_service._import_state))
    tree = ast.parse(src)
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_every_exported_table_is_imported():
    """Loud failure if export bundles a table ``_import_state`` never references.

    Mutation proof: add a spec like ``{"name": "fake_drift_table", ...}`` to
    ``export_service._CRITICAL_TABLES`` → this goes red naming it; remove → green.
    Adding only a ``# fake_drift_table`` comment to ``_import_state`` keeps it red
    (ast ignores comments) — that is the substring-robustness earning its keep.
    """
    exported = _exported_table_names()
    referenced = _import_state_string_literals()
    missing = sorted(exported - referenced)

    assert not missing, (
        "SILENT ROUND-TRIP DROP: these tables are bundled by export_service but "
        f"never referenced in import_service._import_state: {missing}. Export "
        "writes their rows into the ZIP; import never restores them (the "
        "_read_json/_read_jsonl []-floor swallows the missing file with no error). "
        "Wire each into _import_state in FK-safe order."
    )


# ---------------------------------------------------------------------------
# Behavioral round-trip: seed every table, export→import, assert nothing dropped
# ---------------------------------------------------------------------------

RT_FOLDER = "RoundTripRP"

# FK / filter columns whose value must come from a seeded parent or the folder,
# not a generic dummy. Keys are column names as they appear across the schema.
_LINKAGE_COLS = {"session_id", "exchange_id", "thread_id", "schema_id", "manifest_id"}


def _typed_dummy(coltype: str, table: str, name: str):
    """A schema-type-appropriate placeholder for a required, non-linkage column."""
    t = (coltype or "").upper()
    if "INT" in t or "BOOL" in t:
        return 1
    if "REAL" in t or "FLOA" in t or "DOUB" in t:
        return 1.0
    return f"rt-{table}-{name}"[:60]


async def _seed_one_row(db: Database, table: str, folder: str, parents: dict) -> tuple[int, dict]:
    """Insert a single minimal-but-valid row into ``table``.

    Fills (a) the export filter column ``rp_folder``, (b) ``branch`` /
    ``exchange_number`` where present, (c) FK-linkage columns from already-seeded
    ``parents``, and (d) every remaining NOT NULL / PK column with a typed dummy.
    Skips a single INTEGER PK (autoincrement rowid). Schema-adaptive via
    ``PRAGMA table_info`` so it survives column additions without edits.

    Returns ``(lastrowid, row)`` so the caller can register this row as a parent
    (autoinc tables → lastrowid; text/composite PK tables → ``row[pk]``).
    """
    cols = await db.fetch_all(f"PRAGMA table_info({table})")
    pk_cols = [c for c in cols if c["pk"]]
    autoinc_pk = len(pk_cols) == 1 and "INT" in (pk_cols[0]["type"] or "").upper()

    row: dict = {}
    for c in cols:
        name = c["name"]
        if autoinc_pk and c["pk"]:
            continue  # let the rowid alias generate
        if name == "rp_folder":
            row[name] = folder
        elif name == "branch":
            row[name] = "main"
        elif name == "exchange_number":
            row[name] = 1
        elif name in _LINKAGE_COLS:
            row[name] = parents[name]
        elif c["notnull"] or c["pk"]:
            row[name] = _typed_dummy(c["type"], table, name)
        # nullable, non-linkage columns are left unset (NULL) to stay minimal

    col_names = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    fut = await db.enqueue_write(
        f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})", list(row.values())
    )
    return await fut, row


async def _seed_all_tables(db: Database, folder: str) -> None:
    """Seed exactly one row in every export-spec table, parents before children.

    The explicit parent order mirrors the real FK graph (sessions→exchanges,
    plot_threads→thread_*, custom_state_schemas→custom_state_entries,
    analysis_manifests→analysis_manifest_entries). Every other table FKs only into
    these five, so once they exist the remainder can be seeded in any order.
    """
    parents: dict = {}

    # 1. sessions (text PK) → session_id
    _, srow = await _seed_one_row(db, "sessions", folder, parents)
    parents["session_id"] = srow["id"]
    # 2. exchanges (autoinc) → exchange_id
    exch_id, _ = await _seed_one_row(db, "exchanges", folder, parents)
    parents["exchange_id"] = exch_id
    # 3. plot_threads (composite PK) → thread_id
    _, prow = await _seed_one_row(db, "plot_threads", folder, parents)
    parents["thread_id"] = prow["id"]
    # 4. custom_state_schemas (text id) → schema_id
    _, schrow = await _seed_one_row(db, "custom_state_schemas", folder, parents)
    parents["schema_id"] = schrow["id"]
    # 5. analysis_manifests (autoinc) → manifest_id
    man_id, _ = await _seed_one_row(db, "analysis_manifests", folder, parents)
    parents["manifest_id"] = man_id

    seeded = {"sessions", "exchanges", "plot_threads", "custom_state_schemas", "analysis_manifests"}
    specs = export_service._CRITICAL_TABLES + export_service._OPTIONAL_TABLES
    for spec in specs:
        if spec["name"] not in seeded:
            await _seed_one_row(db, spec["name"], folder, parents)


async def test_seeded_round_trip_preserves_every_table(tmp_path):
    """Every export-spec table's rows must survive a real export→import.

    Catches prefix flips, format (jsonl) flips, and import-side FK/order drops
    that the static name guard is blind to: any such drift lands the table's file
    where import never reads it, so it arrives with zero rows in the fresh target.

    Mutation proof: in ``_import_state`` change the ``prefix=`` of a step-10 table
    (e.g. ``trust_baselines``) from ``optional`` to ``state``, or flip a critical
    table's export ``file`` to ``.jsonl`` without ``jsonl=True`` on import — this
    goes red naming the dropped table; revert → green.
    """
    specs = export_service._CRITICAL_TABLES + export_service._OPTIONAL_TABLES

    source_db = Database(tmp_path / "source.db")
    await source_db.initialize()
    target_db = Database(tmp_path / "target.db")
    await target_db.initialize()
    try:
        await _seed_all_tables(source_db, RT_FOLDER)

        # Export (no vault cards — state tables only).
        src_vault = tmp_path / "src_vault"
        src_vault.mkdir()
        buf = await export_rp(source_db, src_vault, RT_FOLDER)

        # Meta-assert: every table was actually seeded AND bundled, so no table
        # can pass the round-trip check vacuously (0 source rows → 0 target rows).
        with zipfile.ZipFile(buf) as zf:
            names = set(zf.namelist())
        missing_from_zip = sorted(
            spec["name"]
            for spec in specs
            if f"state/{spec['file']}" not in names and f"optional/{spec['file']}" not in names
        )
        assert not missing_from_zip, (
            "SEED/EXPORT GAP (would make the round-trip check vacuous): these "
            f"tables produced no export file: {missing_from_zip}. The seeder did "
            "not populate them, or export dropped them — fix before trusting the "
            "round-trip assertion below."
        )

        # Round-trip: import into the fresh target DB (no rp_folder collision).
        buf.seek(0)
        tgt_vault = tmp_path / "tgt_vault"
        tgt_vault.mkdir()
        await import_rp(target_db, tgt_vault, buf.getvalue())

        # The target DB only ever received this one import, so COUNT(*) is a
        # filter-agnostic survival check for every table.
        dropped = []
        for spec in specs:
            count = await target_db.fetch_val(f"SELECT COUNT(*) FROM {spec['name']}")
            if not count:
                dropped.append(spec["name"])

        assert not dropped, (
            "SILENT ROUND-TRIP DROP: these tables were exported with data but "
            f"arrived empty after import: {dropped}. Their export path/format does "
            "not match what _import_state reads (prefix flip, jsonl flip, or an "
            "import-side FK/order drop swallowed by the _read_json []-floor or "
            "INSERT OR IGNORE)."
        )
    finally:
        await source_db.close()
        await target_db.close()
