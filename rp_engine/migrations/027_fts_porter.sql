-- Migration 027: Recreate vectors_fts with the FTS5 porter tokenizer
--
-- The original vectors_fts (001_initial.sql) used the default unicode61
-- tokenizer (case/diacritic folding only, no stemming), so a keyword search
-- for "running" would not match content containing only "ran"... well, "runs".
-- The porter tokenizer stems both indexed content and query terms with the
-- same algorithm, so morphological variants of regular words match.
--
-- This is an external-content FTS table (content='vectors'), so we drop and
-- recreate the virtual table, then repopulate it from the vectors content
-- table via the FTS5 'rebuild' command. The sync triggers (vectors_ai/ad/au)
-- live on the `vectors` table and reference vectors_fts by name, so recreating
-- the table with the same name leaves them valid.
--
-- DROP TABLE IF EXISTS (not CREATE ... IF NOT EXISTS): if the table already
-- exists with the old tokenizer, a guarded CREATE would silently no-op and
-- leave unicode61 in place. The _migrations tracker guarantees once-only runs.

DROP TABLE IF EXISTS vectors_fts;

CREATE VIRTUAL TABLE vectors_fts USING fts5(
    content,
    content='vectors',
    content_rowid='id',
    tokenize='porter unicode61'
);

-- Rebuild the index from the vectors content table.
INSERT INTO vectors_fts(vectors_fts) VALUES('rebuild');
