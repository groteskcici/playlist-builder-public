CREATE TABLE IF NOT EXISTS candidate_state (
    dedupe_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event_id TEXT,
    event_type TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    project_title TEXT NOT NULL,
    event_date DATE,
    spotify_artist_id TEXT,
    effective_status TEXT NOT NULL,
    artist_value_tier TEXT,
    prerelease_uri TEXT,
    resolved_track_count INTEGER NOT NULL DEFAULT 0,
    resolved_track_uris JSONB NOT NULL DEFAULT '[]'::jsonb,
    track_uri_hash TEXT NOT NULL DEFAULT '',
    score_action TEXT NOT NULL DEFAULT 'ignore',
    last_checked_at TIMESTAMPTZ NOT NULL,
    last_change_at TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ NOT NULL,
    publish_pending BOOLEAN NOT NULL DEFAULT FALSE,
    spotify_playlist_id TEXT,
    spotify_playlist_uri TEXT,
    last_published_at TIMESTAMPTZ,
    latest_payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_candidate_state_next_check
    ON candidate_state (next_check_at);

CREATE INDEX IF NOT EXISTS idx_candidate_state_effective_status
    ON candidate_state (effective_status);

CREATE INDEX IF NOT EXISTS idx_candidate_state_publish_pending
    ON candidate_state (publish_pending)
    WHERE publish_pending = TRUE;

CREATE INDEX IF NOT EXISTS idx_candidate_state_score_action
    ON candidate_state (score_action);

CREATE TABLE IF NOT EXISTS validation_snapshots (
    id BIGSERIAL PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    effective_status TEXT NOT NULL,
    resolved_track_count INTEGER NOT NULL DEFAULT 0,
    resolved_track_uris JSONB NOT NULL DEFAULT '[]'::jsonb,
    track_uri_hash TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_validation_snapshots_dedupe_checked
    ON validation_snapshots (dedupe_key, checked_at DESC);
