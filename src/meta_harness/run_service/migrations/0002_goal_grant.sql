-- What a goal's budget has promised to a task's run that has not finished yet. A run is granted its share of
-- what the goal has left before it starts, and what it spends is counted only when it ends, so without this a
-- second task asking in between was promised the same money. One row per task: its newest grant. A row is
-- removed when the task's run is recorded, and counts only while its task is running.

CREATE TABLE goal_grant (
    task_id text PRIMARY KEY,
    goal_id text NOT NULL,
    usd numeric(12, 4) NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX goal_grant_goal ON goal_grant (goal_id);
