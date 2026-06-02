-- Migration 031: context_sent tier tracking (Phase 3 follow-up — tiered context)
-- The context_sent dedup in _filter_sent_cards keys on content_hash, which is
-- TIER-BLIND. Without recording the injection tier, a card sent as `brief`
-- (e.g. semantic 0.8) that rises to `full` (e.g. keyword 1.0) within the stale
-- window is silently suppressed to a reference — the LLM keeps only the brief
-- body, never the upgraded full content. This column lets the resolver re-send
-- when the new tier OUTRANKS the last-sent tier.
-- NULL = legacy/pre-migration row; treated as below `reference`, so the first
-- post-migration send re-emits the card once and backfills the tier (self-healing).
ALTER TABLE context_sent ADD COLUMN sent_tier TEXT;
