-- The planner's goals and the issues that feed them.

CREATE TABLE goal (
    id text PRIMARY KEY,
    created_by text NOT NULL,
    statement text NOT NULL,
    repos text[] NOT NULL,
    label text NOT NULL,
    modality text NOT NULL,
    modality_source text NOT NULL,
    latitude numeric(3, 2) NOT NULL DEFAULT 0,
    auto_merge boolean NOT NULL DEFAULT false,
    max_usd numeric(12, 4),
    priority integer NOT NULL DEFAULT 0,
    status text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz
);

CREATE INDEX goal_status ON goal (status);

-- One row per tracker issue a goal took in: the tracker's state beside the meta-harness's.
CREATE TABLE issue_link (
    ref text PRIMARY KEY,
    goal_id text NOT NULL REFERENCES goal (id),
    repo text NOT NULL,
    task_id text,
    state text NOT NULL,
    depends_on text[] NOT NULL DEFAULT '{}',
    refused_reason text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX issue_link_goal ON issue_link (goal_id);
