CREATE TABLE IF NOT EXISTS candidate_state (
    dedupe_key TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_event_id TEXT,
    event_type TEXT NOT NULL,
    artist_name TEXT NOT NULL,
    project_title TEXT NOT NULL,
    event_date TEXT,
    spotify_artist_id TEXT,
    effective_status TEXT NOT NULL,
    artist_value_tier TEXT,
    prerelease_uri TEXT,
    resolved_track_count INTEGER NOT NULL DEFAULT 0,
    resolved_track_uris TEXT NOT NULL DEFAULT '[]',
    track_uri_hash TEXT NOT NULL DEFAULT '',
    score_action TEXT NOT NULL DEFAULT 'ignore',
    last_checked_at TEXT NOT NULL,
    last_change_at TEXT,
    next_check_at TEXT NOT NULL,
    publish_pending INTEGER NOT NULL DEFAULT 0,
    spotify_playlist_id TEXT,
    spotify_playlist_uri TEXT,
    last_published_at TEXT,
    latest_payload TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_candidate_state_next_check
    ON candidate_state (next_check_at);

CREATE INDEX IF NOT EXISTS idx_candidate_state_effective_status
    ON candidate_state (effective_status);

CREATE INDEX IF NOT EXISTS idx_candidate_state_publish_pending
    ON candidate_state (publish_pending)
    WHERE publish_pending = 1;

CREATE INDEX IF NOT EXISTS idx_candidate_state_score_action
    ON candidate_state (score_action);

CREATE TABLE IF NOT EXISTS validation_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    effective_status TEXT NOT NULL,
    resolved_track_count INTEGER NOT NULL DEFAULT 0,
    resolved_track_uris TEXT NOT NULL DEFAULT '[]',
    track_uri_hash TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_validation_snapshots_dedupe_checked
    ON validation_snapshots (dedupe_key, checked_at DESC);

CREATE TABLE IF NOT EXISTS album_tier_cache (
    cache_key TEXT PRIMARY KEY,
    spotify_artist_id TEXT,
    genius_artist_name TEXT NOT NULL,
    last_popularity INTEGER,
    last_follower_count INTEGER,
    disposition TEXT NOT NULL,
    reason TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    next_check_at TEXT,
    release_date TEXT
);

CREATE INDEX IF NOT EXISTS idx_album_tier_cache_genius_name
    ON album_tier_cache (genius_artist_name);

CREATE INDEX IF NOT EXISTS idx_album_tier_cache_next_check
    ON album_tier_cache (next_check_at);

CREATE INDEX IF NOT EXISTS idx_album_tier_cache_spotify_artist
    ON album_tier_cache (spotify_artist_id);
