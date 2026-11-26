-- A goal's description as an embedding vector, kept so a new goal's description can be compared with the
-- open goals' (planner/similar_goals.py). text_hash: the description it was made from, so a changed one is
-- made again. model: what made it, so a change of model makes it again.
CREATE TABLE planner.goal_embedding (
    goal_id text PRIMARY KEY REFERENCES planner.goal (id) ON DELETE CASCADE,
    model text NOT NULL,
    vector real[] NOT NULL,
    text_hash text NOT NULL,
    made_at timestamptz NOT NULL DEFAULT now()
);
