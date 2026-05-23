-- Migration 026: Add message_mode to exchanges
-- Supports: 'rp' (default), 'ooc', 'direction'
ALTER TABLE exchanges ADD COLUMN message_mode TEXT NOT NULL DEFAULT 'rp';
