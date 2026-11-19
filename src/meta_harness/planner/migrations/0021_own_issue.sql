-- The issues of the meta-harness's own tracker (trackers/own.py): a plan's new tasks that are kept in the
-- meta-harness and in no outside tracker, and every issue of a goal that uses no tracker.
CREATE TABLE IF NOT EXISTS planner.own_issue (
    id text PRIMARY KEY,
    number bigint GENERATED ALWAYS AS IDENTITY,
    repo text NOT NULL,
    title text NOT NULL,
    body text NOT NULL DEFAULT '',
    labels text[] NOT NULL DEFAULT '{}',
    -- open or closed.
    state text NOT NULL DEFAULT 'open',
    -- Where its task stands, as the planner last said: todo, in_progress, in_review or done.
    status text NOT NULL DEFAULT 'todo',
    pr_url text,
    comments jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS own_issue_open ON planner.own_issue (repo) WHERE state = 'open';
