-- A goal's next steps (planner/next_steps.py): what the planner would do next if it carried the work on, each
-- with the statement of a new goal. Kept with the digest of the facts they were written from: read from here
-- until those facts change, and only then written again.

CREATE TABLE goal_next_steps (
    goal_id text PRIMARY KEY REFERENCES goal(id) ON DELETE CASCADE,
    digest text NOT NULL,
    -- Each step: {id, title, why, statement}.
    steps jsonb NOT NULL DEFAULT '[]',
    model text NOT NULL DEFAULT '',
    usd_cost numeric(12, 6) NOT NULL DEFAULT 0,
    written_at timestamptz NOT NULL DEFAULT now()
);

-- The goal a goal was started from, and which of its next steps: a step already started shows its goal, and
-- starting it twice makes one goal.
ALTER TABLE goal ADD COLUMN from_goal text;
ALTER TABLE goal ADD COLUMN from_step text;
CREATE INDEX goal_from_goal ON goal (from_goal) WHERE from_goal IS NOT NULL;
