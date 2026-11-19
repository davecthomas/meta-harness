-- A fix the planner suggests for a plan waiting for approval (planner/suggestions.py): a revised plan, kept
-- apart from the goal's plan versions until the operator accepts it, when it becomes the next version.
CREATE TABLE IF NOT EXISTS planner.plan_suggestion (
    goal_id text NOT NULL REFERENCES planner.goal(id) ON DELETE CASCADE,
    -- The plan version it revises: shown only while that version is still the goal's latest.
    for_version integer NOT NULL,
    -- drafting while its planning task runs; then open, accepted, dismissed, or failed.
    status text NOT NULL DEFAULT 'drafting',
    task_id text,
    -- What it fixes: each of the plan's gaps it settles, with the change that settles it.
    fixes jsonb NOT NULL DEFAULT '[]',
    note text NOT NULL DEFAULT '',
    drafts jsonb NOT NULL DEFAULT '[]',
    items jsonb NOT NULL DEFAULT '[]',
    summary text NOT NULL DEFAULT '',
    gaps jsonb NOT NULL DEFAULT '[]',
    issues jsonb NOT NULL DEFAULT '[]',
    invalid jsonb NOT NULL DEFAULT '[]',
    -- The version it became, once accepted.
    accepted_version integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id, for_version)
);
