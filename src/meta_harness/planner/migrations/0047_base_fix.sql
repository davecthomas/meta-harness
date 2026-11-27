-- A repo whose base branch fails its standing checks, and the one task that fixes it. One row for a repo: the
-- commit the checks failed on, the failing checks with the end of each one's output, the goal whose task
-- found them, and the fix task's issue once it is started (NULL while the goal's operator is asked).

CREATE TABLE planner.base_fix (
    repo text PRIMARY KEY,
    commit_sha text NOT NULL,
    goal_id text NOT NULL,
    checks jsonb NOT NULL DEFAULT '[]',
    ref text,
    found_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz
);

CREATE INDEX base_fix_goal ON planner.base_fix (goal_id);
