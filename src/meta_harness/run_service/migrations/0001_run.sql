-- The meta-harness run service's record of every run and routing decision.

CREATE TABLE routing_decision (
    id bigserial PRIMARY KEY,
    task_id text NOT NULL,
    -- The RunTask workflow's route call number: 1 for the first tier, 2 after an escalation.
    route_no integer NOT NULL,
    route_tier text NOT NULL,
    route_method text NOT NULL,
    route_confidence double precision,
    candidates jsonb NOT NULL,
    route_floor text,
    route_ceiling text,
    classifier_usd numeric(12, 6),
    config_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, route_no)
);

CREATE TABLE run (
    id text PRIMARY KEY,
    task_id text NOT NULL,
    attempt_no integer NOT NULL,
    route_tier text,
    candidate text NOT NULL,
    harness_profile text NOT NULL,
    model text NOT NULL,
    decision text NOT NULL,
    detail text NOT NULL,
    status text,
    end_reason text,
    turns integer,
    tokens_in bigint,
    tokens_out bigint,
    spent_usd numeric(12, 6) NOT NULL DEFAULT 0,
    files_changed jsonb NOT NULL DEFAULT '[]'::jsonb,
    ended_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (task_id, attempt_no)
);

CREATE INDEX run_task ON run (task_id);
