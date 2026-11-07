-- The task service's tasks.

CREATE TABLE task (
    id text PRIMARY KEY,
    caller text NOT NULL,
    external_ref text NOT NULL,
    repo text NOT NULL,
    task_type text NOT NULL,
    parent_task_id text REFERENCES task (id),
    goal_id text,
    depends_on text[] NOT NULL DEFAULT '{}',
    priority integer NOT NULL DEFAULT 0,
    title text NOT NULL,
    description text NOT NULL,
    acceptance_checks jsonb NOT NULL DEFAULT '[]'::jsonb,
    paths text[] NOT NULL DEFAULT '{}',
    complexity text,
    max_tier text,
    max_usd numeric(12, 4),
    pin text,
    status text NOT NULL,
    status_reason text,
    cancel_requested boolean NOT NULL DEFAULT false,
    owner text,
    pr_url text,
    head_sha text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (caller, external_ref)
);

CREATE INDEX task_status_priority ON task (status, priority DESC, created_at);
CREATE INDEX task_goal ON task (goal_id);
CREATE INDEX task_parent ON task (parent_task_id);
