-- Adversarial plan review: a second model, of another family than the planner's, tries to find what is wrong
-- with a goal's plan before the operator approves it. A goal says whether its plans are reviewed, and each
-- review of a plan version is kept with its findings, which the planner then revises the plan for.

ALTER TABLE goal ADD COLUMN adversarial_review boolean NOT NULL DEFAULT false;

CREATE TABLE plan_review (
    goal_id text NOT NULL REFERENCES goal (id),
    -- The plan version it read, and which review of the plan this is, from 1.
    version integer NOT NULL,
    round integer NOT NULL,
    task_id text,
    -- The candidate that reviews, harness:<profile>/<engine>:<model>.
    reviewer text NOT NULL,
    -- running, done, partial (its run ended early, with the findings it had), failed, canceled.
    state text NOT NULL,
    summary text NOT NULL DEFAULT '',
    -- Each finding: id, kind, severity (blocking or advice), says, change, tasks, status.
    findings jsonb NOT NULL DEFAULT '[]',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id, version)
);
