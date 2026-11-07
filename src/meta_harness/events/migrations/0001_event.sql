-- The event service's record of every meta-harness domain event.

CREATE TABLE event_type (
    name text NOT NULL,
    schema_version integer NOT NULL,
    json_schema jsonb NOT NULL,
    retention_class text NOT NULL CHECK (retention_class IN ('short', 'long')),
    PRIMARY KEY (name, schema_version)
);

CREATE TABLE event (
    position bigserial PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    event_type text NOT NULL,
    schema_version integer NOT NULL,
    time_utc timestamptz NOT NULL,
    source text NOT NULL,
    actor text,
    run_id text,
    task_id text,
    session_id text,
    request_id text,
    seq bigint,
    trace_id text,
    config_id text,
    config_version text,
    tags jsonb NOT NULL DEFAULT '{}'::jsonb,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (event_type, schema_version) REFERENCES event_type (name, schema_version)
);

CREATE INDEX event_run_position ON event (run_id, position);
CREATE INDEX event_run_time ON event (run_id, time_utc);
CREATE INDEX event_type_time ON event (event_type, time_utc);
CREATE INDEX event_task_position ON event (task_id, position);
