-- Migration 030 (Phase 5b): lorebook_entries cache/index table.
--
-- The lorebook is a FILE-DROP library: lorebook files (SillyTavern world_info
-- JSON, structured TTRPG docs, markdown) dropped into a per-RP `Lorebooks/`
-- folder or a configurable global library path are the SOURCE OF TRUTH. This
-- table is an INDEX/CACHE only, rebuilt from those files by LorebookIndexer
-- (mirrors how story_cards caches .md files). The API never modifies the files.
--
-- NO `branch` column by design: lorebook entries are reference content (like
-- story cards) — RP-global or global-library, visible on every branch of an RP.
-- (Match-time STATE conditions still resolve by the live request branch via
-- TriggerEvaluator; that is a separate concern from entry storage scope.)
--
-- Matching REUSES services/trigger_evaluator.py — `conditions` is a JSON list of
-- TriggerEvaluator condition dicts combined via `match_mode`. Compound/layered
-- scope is expressed as MULTIPLE condition dicts + match_mode='all', never a
-- nested expression string (the evaluator is a flat parser and would mis-match
-- a nested `all(any(...), near(...))` — confirmed Phase 5b).

CREATE TABLE IF NOT EXISTS lorebook_entries (
    id INTEGER PRIMARY KEY,
    source_path TEXT NOT NULL,       -- file this entry came from (re-index key)
    section_path TEXT,               -- JSON key path for structured docs; NULL for ST/flat
    scope TEXT NOT NULL,             -- 'rp' | 'global'
    rp_folder TEXT,                  -- set when scope='rp'; NULL for global
    name TEXT,                       -- display/identifier (ST comment, section path, etc.)
    keywords TEXT,                   -- derived/primary keys (JSON array)
    conditions TEXT,                 -- TriggerEvaluator condition list (JSON); NULL = keyword-only
    match_mode TEXT NOT NULL DEFAULT 'all',
    content TEXT NOT NULL,
    budget_weight INTEGER NOT NULL DEFAULT 1,
    always_on INTEGER NOT NULL DEFAULT 0,
    depth INTEGER,                   -- optional Phase-4 injection depth
    enabled INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT,               -- re-index guard
    created_at TEXT,
    updated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_lorebook_scope ON lorebook_entries(scope, rp_folder);
CREATE INDEX IF NOT EXISTS idx_lorebook_source ON lorebook_entries(source_path);
