CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  base_url TEXT NOT NULL,
  request_interval_ms INTEGER NOT NULL DEFAULT 250,
  status VARCHAR(32) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS discovery_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error TEXT
);

CREATE TABLE IF NOT EXISTS service_credentials (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  kind VARCHAR(32) NOT NULL,
  placement VARCHAR(32) NOT NULL,
  param_name VARCHAR(255) NOT NULL,
  value TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS service_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  source_run_id UUID REFERENCES discovery_runs(id) ON DELETE SET NULL,
  type VARCHAR(16) NOT NULL,
  sequence_id UUID NOT NULL,
  sequence_order INTEGER NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  method VARCHAR(16) NOT NULL,
  path TEXT NOT NULL,
  payload_ref TEXT,
  response_code INTEGER,
  response_ref TEXT,
  latency_ms INTEGER,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  executed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS insights (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  discovery_run_id UUID REFERENCES discovery_runs(id) ON DELETE SET NULL,
  kind VARCHAR(32) NOT NULL,
  title VARCHAR(255) NOT NULL,
  content TEXT NOT NULL,
  confidence NUMERIC NOT NULL DEFAULT 0,
  status VARCHAR(32) NOT NULL DEFAULT 'proposed',
  version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS insight_evidence (
  insight_id UUID NOT NULL REFERENCES insights(id) ON DELETE CASCADE,
  service_request_id UUID NOT NULL REFERENCES service_requests(id) ON DELETE CASCADE,
  note TEXT,
  PRIMARY KEY (insight_id, service_request_id)
);

CREATE TABLE IF NOT EXISTS mimic_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL REFERENCES services(id) ON DELETE CASCADE,
  status VARCHAR(32) NOT NULL DEFAULT 'created',
  iterations INTEGER NOT NULL DEFAULT 0,
  match_rate NUMERIC,
  code_ref TEXT,
  lambda_version VARCHAR(255),
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  error TEXT
);

CREATE TABLE IF NOT EXISTS mimic_run_insights (
  mimic_run_id UUID NOT NULL REFERENCES mimic_runs(id) ON DELETE CASCADE,
  insight_id UUID NOT NULL REFERENCES insights(id) ON DELETE RESTRICT,
  PRIMARY KEY (mimic_run_id, insight_id)
);

CREATE TABLE IF NOT EXISTS replay_results (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  mimic_run_id UUID NOT NULL REFERENCES mimic_runs(id) ON DELETE CASCADE,
  service_request_id UUID NOT NULL REFERENCES service_requests(id) ON DELETE CASCADE,
  iteration INTEGER NOT NULL,
  matched BOOLEAN NOT NULL,
  diff JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS run_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  discovery_run_id UUID REFERENCES discovery_runs(id) ON DELETE CASCADE,
  mimic_run_id UUID REFERENCES mimic_runs(id) ON DELETE CASCADE,
  step VARCHAR(120) NOT NULL,
  level VARCHAR(16) NOT NULL DEFAULT 'info',
  message TEXT NOT NULL,
  progress_done INTEGER,
  progress_total INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT run_events_one_parent CHECK (
    (discovery_run_id IS NOT NULL)::integer + (mimic_run_id IS NOT NULL)::integer = 1
  )
);

CREATE TABLE IF NOT EXISTS cloned_services (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  service_id UUID NOT NULL UNIQUE REFERENCES services(id) ON DELETE CASCADE,
  slug VARCHAR(120) NOT NULL UNIQUE,
  active_mimic_run_id UUID,
  enabled BOOLEAN NOT NULL DEFAULT false,
  token_hash VARCHAR(255),
  token_prefix VARCHAR(32),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mimic_records (
  cloned_service_id UUID NOT NULL REFERENCES cloned_services(id) ON DELETE CASCADE,
  namespace VARCHAR(120) NOT NULL,
  table_name VARCHAR(120) NOT NULL,
  record_id VARCHAR(255) NOT NULL,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (cloned_service_id, namespace, table_name, record_id)
);

