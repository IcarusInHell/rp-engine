-- Migration 028: Trigger sticky/delay (Phase 5a)
-- sticky_turns:  trigger keeps re-injecting for N turns after a condition-driven
--                fire (1 = no persistence, the pre-5a behavior).
-- delay_turns:   require N consecutive matching turns before firing
--                (0 or 1 = fire on first match, the pre-5a behavior).
-- consecutive_matches: mutable counter for the delay window. Stored on the shared
--                definition row (like last_fired_turn), so it can bleed across
--                branches — cosmetic for read-only injection (documented in 5a notes).
ALTER TABLE situational_triggers ADD COLUMN sticky_turns INTEGER DEFAULT 1;
ALTER TABLE situational_triggers ADD COLUMN delay_turns INTEGER DEFAULT 0;
ALTER TABLE situational_triggers ADD COLUMN consecutive_matches INTEGER DEFAULT 0;
