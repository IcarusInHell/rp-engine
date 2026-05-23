-- Migration 025: Add source column to exchange_variants
-- Tracks how a variant was created: 'llm' (generated), 'manual_edit' (user edit), 'continue' (continuation)

ALTER TABLE exchange_variants ADD COLUMN source TEXT NOT NULL DEFAULT 'llm';
