-- 011_risk_profile_changes.sql
--
-- ADR 0054: audit table for risk-profile switches and startup defaults.
-- Every change attempt (successful, refused, or implicit-startup) writes one row.

CREATE TABLE IF NOT EXISTS risk_profile_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    from_profile TEXT NOT NULL,
    to_profile TEXT NOT NULL,
    actor TEXT NOT NULL,                   -- 'gui' | 'cli' | 'startup'
    confirmation_method TEXT NOT NULL,     -- 'typed' | 'single_click' | 'none'
    context_mode TEXT NOT NULL,            -- 'paper' | 'micro_live' | 'live'
    runners_active_count INTEGER NOT NULL DEFAULT 0,
    success INTEGER NOT NULL,              -- 1 = applied, 0 = refused/failed
    failure_reason TEXT                    -- nullable; populated when success=0
);

CREATE INDEX IF NOT EXISTS idx_risk_profile_changes_time
    ON risk_profile_changes (recorded_at);
