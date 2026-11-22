-- A specification loaded with a goal: a product requirements document, a design document or the like, as
-- text. The goal's planning task breaks it down into tasks, for a goal whose work has no issues yet. One per
-- goal, kept as it was loaded.

CREATE TABLE goal_spec (
    goal_id text PRIMARY KEY REFERENCES goal (id),
    name text NOT NULL,
    body text NOT NULL,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
