-- ============================================================
-- Rowbutt Aggregator — Central SQLite Schema
-- Runs on the central server. Stores daily summaries pulled
-- from each agent, pricing cache, and computed cost reports.
-- ============================================================

-- Daily summary per machine (pulled from agent APIs)
CREATE TABLE IF NOT EXISTS daily_summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname        TEXT    NOT NULL,  -- machine name
    date            TEXT    NOT NULL,  -- ISO date
    -- Token data (merged from agent)
    total_input     INTEGER NOT NULL DEFAULT 0,
    total_output    INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    model_breakdown TEXT,              -- JSON per-model token counts
    -- System data (averaged from agent)
    avg_mem_pct     REAL,
    avg_temp_cpu    REAL,
    avg_temp_gpu    REAL,
    avg_gpu_power_w REAL,
    max_temp_cpu    REAL,
    max_temp_gpu    REAL,
    inference_time_minutes REAL,       -- estimated from token events or GPU activity
    -- Agent metadata
    agent_version   TEXT,
    collectors_active TEXT,            -- JSON list of active collectors
    raw_payload     TEXT,              -- full JSON response from agent (for re-processing)
    created_at      TEXT    DEFAULT (datetime('now')),
    UNIQUE(hostname, date)
);

-- Frontier pricing cache (daily snapshots)
CREATE TABLE IF NOT EXISTS pricing_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT    NOT NULL,  -- ISO date this price was current
    model           TEXT    NOT NULL,  -- canonical model name
    provider        TEXT    NOT NULL,  -- "openai", "anthropic", "google", "deepseek", etc.
    input_price     REAL    NOT NULL,  -- $ per 1M input tokens
    output_price    REAL    NOT NULL,  -- $ per 1M output tokens
    source          TEXT,              -- URL or reference
    UNIQUE(date, model)
);

-- Default pricing for common models (seeded on first init)
INSERT OR IGNORE INTO pricing_cache (date, model, provider, input_price, output_price, source)
VALUES
    ('2026-01-01', 'deepseek-ai/DeepSeek-V4-Flash', 'deepseek', 0.15, 0.60, 'config-default'),
    ('2026-01-01', 'deepseek-ai/DeepSeek-R1',       'deepseek', 0.55, 2.19, 'config-default'),
    ('2026-01-01', 'qwen/Qwen2.5-72B-Instruct',   'deepseek', 0.35, 1.20, 'config-default'),
    ('2026-01-01', 'mistralai/Mistral-Large',       'mistral',  2.00, 6.00, 'config-default'),
    ('2026-01-01', 'meta-llama/Llama-3.1-70B',     'together', 0.88, 0.88, 'config-default'),
    ('2026-01-01', 'gpt-4o',                         'openai',   2.50, 10.00, 'config-default'),
    ('2026-01-01', 'gpt-4o-mini',                    'openai',   0.15, 0.60, 'config-default'),
    ('2026-01-01', 'claude-3-5-sonnet-20241022',     'anthropic', 3.00, 15.00, 'config-default'),
    ('2026-01-01', 'claude-3-opus-20240229',         'anthropic', 15.00, 75.00, 'config-default'),
    ('2026-01-01', 'gemini-1.5-pro',                 'google',   1.25, 5.00, 'config-default'),
    ('2026-01-01', 'gemini-1.5-flash',               'google',   0.075, 0.30, 'config-default');

-- Computed cost reports (one row per machine per day)
CREATE TABLE IF NOT EXISTS cost_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname        TEXT    NOT NULL,
    date            TEXT    NOT NULL,  -- ISO date
    -- Electricity
    inference_hours      REAL,        -- total hours of inference activity
    system_power_w       REAL,        -- configured system baseline (W)
    gpu_avg_power_w      REAL,        -- average GPU power draw (W)
    total_power_kwh      REAL,        -- computed kWh
    electricity_cost     REAL,        -- $ at $0.11/kWh
    -- Frontier costs
    frontier_provider    TEXT,        -- which provider was used for comparison
    frontier_input_cost  REAL,        -- $ for input tokens
    frontier_output_cost REAL,        -- $ for output tokens
    frontier_total_cost  REAL,        -- total $ for tokens
    -- Savings
    savings              REAL,        -- frontier_total_cost - electricity_cost
    -- Reference
    pricing_cache_id     INTEGER REFERENCES pricing_cache(id),
    cost_per_1m_tokens   REAL,        -- blended cost per 1M tokens
    created_at           TEXT DEFAULT (datetime('now')),
    UNIQUE(hostname, date)
);

-- Aggregator metadata (singleton row)
CREATE TABLE IF NOT EXISTS aggregator_meta (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL,
    updated_at      TEXT    DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO aggregator_meta (key, value) VALUES ('schema_version', '1');
INSERT OR IGNORE INTO aggregator_meta (key, value) VALUES ('aggregator_version', '0.1.0');
INSERT OR IGNORE INTO aggregator_meta (key, value) VALUES ('created_at', datetime('now'));
