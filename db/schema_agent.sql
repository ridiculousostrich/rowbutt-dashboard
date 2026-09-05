-- ============================================================
-- Rowbutt Agent — Local SQLite Schema
-- Each machine running an LLM endpoint has one of these.
-- Stores raw collector samples and pre-computed daily rollups.
-- ============================================================

-- Raw token usage events from LLM endpoint polls
CREATE TABLE IF NOT EXISTS token_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at     TEXT    NOT NULL,  -- ISO timestamp when agent recorded this
    model           TEXT    NOT NULL,  -- model name, e.g. "deepseek-ai/DeepSeek-V4-Flash"
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    session_id      TEXT,              -- LLM session ID, if available
    source          TEXT,              -- "ollama", "open-webui", "openai-compatible"
    bucket_hour     INTEGER,           -- which 4-hour bucket this falls in (0,4,8,12,16,20)
    bucket_date     TEXT               -- ISO date for the bucket
);

CREATE INDEX IF NOT EXISTS idx_token_date ON token_events(bucket_date, bucket_hour);
CREATE INDEX IF NOT EXISTS idx_token_model ON token_events(model);

-- Raw system metric samples (memory, temps, GPU, load)
CREATE TABLE IF NOT EXISTS system_samples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at      TEXT    NOT NULL,  -- ISO timestamp
    mem_total_gb    REAL,
    mem_used_gb     REAL,
    mem_pct         REAL,
    temp_cpu_avg    REAL,
    temp_cpu_max    REAL,
    temp_gpu        REAL,
    gpu_power_w     REAL,
    gpu_util_pct    REAL,
    load_1m         REAL,
    load_5m         REAL,
    load_15m        REAL,
    bucket_hour      INTEGER,
    bucket_date      TEXT
);

CREATE INDEX IF NOT EXISTS idx_sys_date ON system_samples(bucket_date, bucket_hour);

-- Pre-computed 4-hour rollups for quick day-summary generation
CREATE TABLE IF NOT EXISTS daily_rollups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,  -- ISO date
    bucket_hour     INTEGER NOT NULL,  -- 0, 4, 8, 12, 16, 20
    -- Token summary
    total_input     INTEGER NOT NULL DEFAULT 0,
    total_output    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    -- Per-model token breakdown (stored as JSON)
    model_breakdown TEXT,              -- JSON: {"model": {"input": N, "output": N, "sessions": N}}
    -- System summary
    avg_mem_pct     REAL,
    avg_temp_cpu    REAL,
    avg_temp_gpu    REAL,
    avg_gpu_power_w REAL,
    max_temp_cpu    REAL,
    max_temp_gpu    REAL,
    -- Timing
    sample_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(date, bucket_hour)
);

-- Agent metadata (singleton row — tracks agent state)
CREATE TABLE IF NOT EXISTS agent_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL,
    updated_at      TEXT    DEFAULT (datetime('now'))
);

-- Seed agent metadata
INSERT OR IGNORE INTO agent_meta (key, value) VALUES ('schema_version', '1');
INSERT OR IGNORE INTO agent_meta (key, value) VALUES ('agent_version', '0.1.0');
INSERT OR IGNORE INTO agent_meta (key, value) VALUES ('created_at', datetime('now'));
