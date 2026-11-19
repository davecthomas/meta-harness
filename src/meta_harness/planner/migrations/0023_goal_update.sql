-- The planner's updates on a goal (planner/updates.py): what it tells the operator, in plain language, each time
-- something that matters changes. `digest` is what the goal's state came to when the update was written, so
-- the next one is written only when that has changed.
CREATE TABLE IF NOT EXISTS planner.goal_update (
    goal_id text NOT NULL REFERENCES planner.goal(id) ON DELETE CASCADE,
    number integer NOT NULL,
    digest text NOT NULL,
    headline text NOT NULL,
    body text NOT NULL,
    -- What needs the operator, each with what to do and the issue or task it is about.
    needs_you jsonb NOT NULL DEFAULT '[]',
    next text NOT NULL DEFAULT '',
    model text NOT NULL DEFAULT '',
    usd_cost text NOT NULL DEFAULT '0',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id, number)
);
