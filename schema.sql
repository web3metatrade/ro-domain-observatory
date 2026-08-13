PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    url TEXT NOT NULL,
    access_method TEXT NOT NULL,
    coverage TEXT NOT NULL,
    license TEXT,
    redistribution_status TEXT NOT NULL CHECK (
        redistribution_status IN (
            'allowed', 'allowed_with_attribution', 'permission_held',
            'research_only', 'restricted', 'unclear', 'metadata_only'
        )
    ),
    public_export INTEGER NOT NULL DEFAULT 0 CHECK (public_export IN (0, 1)),
    import_status TEXT NOT NULL DEFAULT 'catalogued',
    notes TEXT,
    last_checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domains (
    domain TEXT PRIMARY KEY,
    unicode_domain TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS hostnames (
    hostname TEXT PRIMARY KEY,
    registrable_domain TEXT NOT NULL REFERENCES domains(domain),
    unicode_hostname TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS domain_sources (
    domain TEXT NOT NULL REFERENCES domains(domain),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    best_rank INTEGER,
    metadata_json TEXT,
    PRIMARY KEY (domain, source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS hostname_sources (
    hostname TEXT NOT NULL REFERENCES hostnames(hostname),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    best_rank INTEGER,
    PRIMARY KEY (hostname, source_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS import_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    input_uri TEXT NOT NULL,
    input_sha256 TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    rows_read INTEGER NOT NULL DEFAULT 0,
    hostnames_accepted INTEGER NOT NULL DEFAULT 0,
    domains_new INTEGER NOT NULL DEFAULT 0,
    hostnames_new INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_hostnames_domain ON hostnames(registrable_domain);
CREATE INDEX IF NOT EXISTS idx_domain_sources_source ON domain_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_hostname_sources_source ON hostname_sources(source_id);
CREATE INDEX IF NOT EXISTS idx_import_runs_source ON import_runs(source_id, started_at);
