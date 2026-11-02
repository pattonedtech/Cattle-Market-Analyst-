-- PostgreSQL target schema for a future authenticated shared-write backend.
-- The current GitHub Pages deployment remains read-only; this migration is not deployed.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS market_sources (
    source_id text PRIMARY KEY,
    source_name text NOT NULL,
    domain text,
    source_kind text NOT NULL,
    source_url text,
    is_active boolean NOT NULL DEFAULT true,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS market_locations (
    market_id text PRIMARY KEY,
    market_name text NOT NULL,
    state text,
    county text,
    sale_region text,
    latitude numeric(9,6),
    longitude numeric(9,6),
    geography_method text NOT NULL DEFAULT 'not_verified'
        CHECK (geography_method IN ('verified_market_registry','source_reported','regional_report','not_verified')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((latitude IS NULL) = (longitude IS NULL))
);

CREATE TABLE IF NOT EXISTS market_observations (
    observation_id text PRIMARY KEY,
    source_id text NOT NULL REFERENCES market_sources(source_id),
    market_id text REFERENCES market_locations(market_id),
    reporting_state text,
    reporting_county text,
    reporting_region text,
    geography_method text NOT NULL DEFAULT 'not_verified'
        CHECK (geography_method IN ('verified_market_registry','source_reported','regional_report','not_verified')),
    sale_date date NOT NULL,
    category text NOT NULL,
    sex text,
    breed text,
    animal_origin text,
    corriente_origin text NOT NULL DEFAULT 'not_applicable'
        CHECK (corriente_origin IN ('Native Corriente','Mexican M-branded Corriente','unspecified','not_applicable')),
    sale_regionality text,
    weight_lb numeric(10,2),
    weight_low_lb numeric(10,2),
    weight_high_lb numeric(10,2),
    weight_basis text NOT NULL DEFAULT 'not_reported'
        CHECK (weight_basis IN ('reported_average','published_class_midpoint','published_class_range','not_reported')),
    head_count integer CHECK (head_count IS NULL OR head_count > 0),
    price numeric(12,2) NOT NULL CHECK (price > 0),
    price_low numeric(12,2),
    price_high numeric(12,2),
    price_unit text NOT NULL CHECK (price_unit IN ('USD/cwt','USD/head','USD/family')),
    price_basis text NOT NULL DEFAULT 'unclassified',
    record_type text NOT NULL,
    source_url text NOT NULL CHECK (source_url LIKE 'https://%'),
    source_hash text,
    raw_payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    qa_status text NOT NULL DEFAULT 'accepted'
        CHECK (qa_status IN ('accepted','warning')),
    qa_warnings text[] NOT NULL DEFAULT ARRAY[]::text[],
    collected_at timestamptz NOT NULL DEFAULT now(),
    CHECK (weight_low_lb IS NULL OR weight_high_lb IS NULL OR weight_low_lb <= weight_high_lb),
    CHECK (price_low IS NULL OR price_high IS NULL OR price_low <= price_high)
);

CREATE INDEX IF NOT EXISTS market_observations_date_idx ON market_observations (sale_date DESC);
CREATE INDEX IF NOT EXISTS market_observations_category_unit_idx ON market_observations (category, price_unit);
CREATE INDEX IF NOT EXISTS market_observations_geography_idx ON market_observations (market_id);
CREATE INDEX IF NOT EXISTS market_observations_source_series_idx ON market_observations (record_type, price_basis);
CREATE INDEX IF NOT EXISTS market_observations_corriente_idx ON market_observations (corriente_origin, category);
CREATE INDEX IF NOT EXISTS market_observations_weight_idx ON market_observations (weight_low_lb, weight_high_lb, weight_lb);

CREATE TABLE IF NOT EXISTS quarantined_observations (
    quarantine_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    observation_id text,
    collection_name text NOT NULL CHECK (collection_name IN ('market_observations','cow_market_observations')),
    source_id text REFERENCES market_sources(source_id),
    source_url text,
    original_payload jsonb NOT NULL,
    rejection_codes text[] NOT NULL,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    resolution text,
    CHECK (resolved_at IS NULL OR resolution IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS quarantined_observations_unresolved_idx
    ON quarantined_observations (first_seen_at DESC) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    status text NOT NULL CHECK (status IN ('running','success','partial','failed')),
    source_health jsonb NOT NULL DEFAULT '{}'::jsonb,
    accepted_count integer NOT NULL DEFAULT 0,
    quarantined_count integer NOT NULL DEFAULT 0,
    duplicate_count integer NOT NULL DEFAULT 0,
    commit_sha text,
    report_url text,
    error_summary text
);

CREATE TABLE IF NOT EXISTS benchmark_snapshots (
    benchmark_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id uuid REFERENCES collection_runs(run_id),
    as_of date NOT NULL,
    geography_level text NOT NULL CHECK (geography_level IN ('western','state','regional','county','market')),
    geography_code text NOT NULL,
    region_label text,
    category text NOT NULL,
    price_unit text NOT NULL CHECK (price_unit IN ('USD/cwt','USD/head','USD/family')),
    source_series text NOT NULL,
    source_record_types text[] NOT NULL DEFAULT ARRAY[]::text[],
    corriente_origin text NOT NULL DEFAULT 'not_applicable'
        CHECK (corriente_origin IN ('Native Corriente','Mexican M-branded Corriente','unspecified','not_applicable')),
    period_days integer NOT NULL DEFAULT 0 CHECK (period_days >= 0),
    weight_cohort text NOT NULL DEFAULT 'all',
    weight_low_lb numeric(10,2),
    weight_high_lb numeric(10,2),
    average_price numeric(12,2),
    low_price numeric(12,2),
    high_price numeric(12,2),
    observation_count integer NOT NULL CHECK (observation_count >= 0),
    head_count bigint,
    average_method text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS benchmark_snapshots_unique_idx
    ON benchmark_snapshots (as_of, geography_level, geography_code, COALESCE(region_label, ''),
                            category, price_unit, source_series, corriente_origin, period_days, weight_cohort);
CREATE INDEX IF NOT EXISTS benchmark_snapshots_lookup_idx
    ON benchmark_snapshots (as_of DESC, category, price_unit, source_series, geography_level);

-- Deliberately no public INSERT/UPDATE policy is defined here. Before enabling
-- shared writes, deploy an authenticated API, enable row-level security, and
-- grant writes only to authenticated roles with explicit permissions.
