-- Every remedy the supervisor applied to a stuck task, one row per try (supervisor/remedies). The rows are
-- the record a person, a page or the planner's update reads to learn what was done for a task, and the
-- count a remedy's limit on tries is checked against.

CREATE TABLE remedy (
    id bigserial PRIMARY KEY,
    task_id text NOT NULL,
    goal_id text,
    -- The situation the task was found in (rules.decide) and the remedy applied for it.
    situation text NOT NULL,
    remedy text NOT NULL,
    -- Which try this was, and the most tries the remedy makes before it asks an operator (null: no limit).
    try integer NOT NULL,
    of integer,
    -- started, done or failed (remedies.OUTCOMES).
    outcome text NOT NULL,
    -- Why it was needed, what was done, and what happens next, each a plain sentence.
    because text NOT NULL,
    did text NOT NULL,
    next text NOT NULL,
    error text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX remedy_task ON remedy (task_id, created_at);
CREATE INDEX remedy_goal ON remedy (goal_id, created_at);
