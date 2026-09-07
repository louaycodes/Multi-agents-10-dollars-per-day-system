-- ==============================================================================
-- Multi-Agent Autonomous Revenue System — PostgreSQL Schema
-- Version: 1.0
-- ==============================================================================

-- Enable UUID extension (optional, for future use)
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ==============================================================================
-- TABLE: daily_results
-- One row per calendar day — final end-of-day P&L summary
-- ==============================================================================
CREATE TABLE IF NOT EXISTS daily_results (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    revenue_total   FLOAT NOT NULL DEFAULT 0.0,
    cost_total      FLOAT NOT NULL DEFAULT 0.0,
    profit          FLOAT GENERATED ALWAYS AS (revenue_total - cost_total) STORED,
    target          FLOAT NOT NULL DEFAULT 10.0,
    target_met      BOOLEAN GENERATED ALWAYS AS (revenue_total - cost_total >= target) STORED,
    revenue_by_niche JSONB DEFAULT '{}'::jsonb,
    -- e.g. {"reselling": 5.0, "gigs": 3.0, "affiliate": 2.0}
    transactions_count INT NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_daily_results_date ON daily_results(date DESC);

-- ==============================================================================
-- TABLE: transactions
-- Every action taken by any agent — granular audit trail
-- ==============================================================================
CREATE TABLE IF NOT EXISTS transactions (
    id              SERIAL PRIMARY KEY,
    date            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    niche           VARCHAR(20) NOT NULL CHECK (niche IN ('reselling', 'gigs', 'affiliate')),
    platform        VARCHAR(50),
    -- e.g. 'facebook_marketplace', 'shopee', 'mturk', 'fiverr', 'quora', 'reddit'
    agent_action    VARCHAR(50) NOT NULL,
    -- e.g. 'product_listed', 'hit_accepted', 'answer_posted', 'sale_confirmed'
    resource_id     VARCHAR(255),
    -- platform-specific ID: listing_id, hit_id, post_id, etc.
    revenue         FLOAT,
    cost            FLOAT NOT NULL DEFAULT 0.0,
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'active', 'completed', 'paid', 'failed')),
    fulfillment_status VARCHAR(20) DEFAULT NULL
                    CHECK (fulfillment_status IN ('pending', 'shipped', 'delivered', 'completed', NULL)),
    revenue_received_at TIMESTAMP WITH TIME ZONE,
    affiliate_clicks INT DEFAULT 0,
    metadata        JSONB DEFAULT '{}'::jsonb,
    -- e.g. {"product_name": "Anti-slip pad", "cost_price": 0.5, "list_price": 3.0}
    error_msg       TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_niche ON transactions(niche);
CREATE INDEX IF NOT EXISTS idx_transactions_status ON transactions(status);
CREATE INDEX IF NOT EXISTS idx_transactions_platform ON transactions(platform);

-- ==============================================================================
-- TABLE: opportunities
-- Scored opportunities discovered by Hunter agent each day
-- ==============================================================================
CREATE TABLE IF NOT EXISTS opportunities (
    id                  SERIAL PRIMARY KEY,
    date                DATE NOT NULL,
    niche               VARCHAR(20) NOT NULL CHECK (niche IN ('reselling', 'gigs', 'affiliate')),
    type                VARCHAR(50) NOT NULL,
    -- e.g. 'product_listing', 'mturk_hit', 'quora_answer', 'reddit_post'
    title               VARCHAR(500) NOT NULL,
    source_url          TEXT,
    estimated_roi       FLOAT NOT NULL DEFAULT 0.0,
    feasibility_score   FLOAT NOT NULL DEFAULT 0.0 CHECK (feasibility_score BETWEEN 0 AND 10),
    effort_hours        FLOAT NOT NULL DEFAULT 1.0,
    time_to_profit      FLOAT NOT NULL DEFAULT 1.0,
    -- 1.0 = same day, 0.5 = 2-3 days, 0.25 = 1 week
    composite_score     FLOAT GENERATED ALWAYS AS (
                            (estimated_roi * feasibility_score * time_to_profit) /
                            GREATEST(effort_hours, 0.1)
                        ) STORED,
    competition_level   VARCHAR(10) DEFAULT 'medium'
                        CHECK (competition_level IN ('low', 'medium', 'high')),
    capital_required    FLOAT NOT NULL DEFAULT 0.0,
    risk_level          VARCHAR(10) DEFAULT 'low'
                        CHECK (risk_level IN ('low', 'medium', 'high')),
    executed            BOOLEAN NOT NULL DEFAULT FALSE,
    execution_txn_id    INT REFERENCES transactions(id) ON DELETE SET NULL,
    notes               TEXT,
    created_at          TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_opportunities_date ON opportunities(date DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_niche ON opportunities(niche);
CREATE INDEX IF NOT EXISTS idx_opportunities_score ON opportunities(composite_score DESC);
CREATE INDEX IF NOT EXISTS idx_opportunities_executed ON opportunities(executed);

-- ==============================================================================
-- TABLE: alerts
-- System alerts generated by Tracker and Optimizer
-- ==============================================================================
CREATE TABLE IF NOT EXISTS alerts (
    id              SERIAL PRIMARY KEY,
    date            TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    alert_type      VARCHAR(50) NOT NULL
                    CHECK (alert_type IN ('low_pnl', 'api_error', 'account_issue', 'optimization_triggered')),
    severity        VARCHAR(10) NOT NULL DEFAULT 'medium'
                    CHECK (severity IN ('low', 'medium', 'high')),
    message         TEXT NOT NULL,
    agent_triggered VARCHAR(30) NOT NULL,
    -- 'manager' | 'hunter' | 'executor' | 'tracker' | 'optimizer'
    action_taken    TEXT,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at     TIMESTAMP WITH TIME ZONE,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_date ON alerts(date DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_type ON alerts(alert_type);
CREATE INDEX IF NOT EXISTS idx_alerts_resolved ON alerts(resolved);

-- ==============================================================================
-- TABLE: performance_metrics
-- Rolling niche performance stats (updated by Manager at 06:00)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS performance_metrics (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL,
    niche           VARCHAR(20) NOT NULL CHECK (niche IN ('reselling', 'gigs', 'affiliate')),
    avg_revenue_7d  FLOAT NOT NULL DEFAULT 0.0,
    avg_revenue_30d FLOAT NOT NULL DEFAULT 0.0,
    success_rate_7d FLOAT NOT NULL DEFAULT 0.0 CHECK (success_rate_7d BETWEEN 0 AND 1),
    total_revenue   FLOAT NOT NULL DEFAULT 0.0,
    best_performer  VARCHAR(255),
    -- e.g. "Anti-slip phone pad" or "mturk_transcription_hit"
    recommendation  TEXT,
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    UNIQUE (date, niche)
);

CREATE INDEX IF NOT EXISTS idx_perf_metrics_date ON performance_metrics(date DESC);
CREATE INDEX IF NOT EXISTS idx_perf_metrics_niche ON performance_metrics(niche);

-- ==============================================================================
-- TABLE: daily_plans
-- Manager's daily allocation plan (for audit + LLM reference)
-- ==============================================================================
CREATE TABLE IF NOT EXISTS daily_plans (
    id              SERIAL PRIMARY KEY,
    date            DATE NOT NULL UNIQUE,
    target          FLOAT NOT NULL DEFAULT 10.0,
    allocations     JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- e.g. {"reselling": 0.5, "gigs": 0.35, "affiliate": 0.15}
    priority_niches JSONB NOT NULL DEFAULT '[]'::jsonb,
    strategy_notes  TEXT,
    llm_reasoning   TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- ==============================================================================
-- FUNCTION: update_updated_at column trigger
-- ==============================================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER update_daily_results_updated_at
    BEFORE UPDATE ON daily_results
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_transactions_updated_at
    BEFORE UPDATE ON transactions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ==============================================================================
-- SEED: Insert Day 0 baseline (avoids NULL on first Manager run)
-- ==============================================================================
INSERT INTO daily_results (date, revenue_total, cost_total, target, revenue_by_niche, notes)
VALUES (CURRENT_DATE - INTERVAL '1 day', 0.0, 0.0, 10.0,
        '{"reselling": 0, "gigs": 0, "affiliate": 0}'::jsonb,
        'Baseline seed — system first run')
ON CONFLICT (date) DO NOTHING;
