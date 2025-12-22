-- Migration 011: add session_cookie column to api_keys for Kodosumi JWT support

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS session_cookie TEXT;
