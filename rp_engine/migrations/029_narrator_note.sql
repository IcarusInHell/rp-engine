-- Migration 029: Session narrator's note (Phase 5a)
-- narrator_note:        session-persistent GM steering text, injected as a system
--                       message at narrator_note_depth (independent of the global
--                       prompt.injection toggle — its own opt-in via the endpoint).
-- narrator_note_depth:  injection depth (>= 1; depth 0 would never be emitted).
ALTER TABLE sessions ADD COLUMN narrator_note TEXT;
ALTER TABLE sessions ADD COLUMN narrator_note_depth INTEGER DEFAULT 2;
