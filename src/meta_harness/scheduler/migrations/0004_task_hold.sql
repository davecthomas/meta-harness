-- Why each queued task the scheduler holds is waiting: one row per held task, removed when the task starts or
-- is no longer queued. The reasons used to be in the scheduler's memory, so every restart recorded them all
-- again as task_held events, and nothing but the event log could say why a task waited.

CREATE TABLE task_hold (
    task_id text PRIMARY KEY,
    -- goal_budget, goal_status, goal_parallel, system_parallel, repo_area, kill_switch, draining or start_failed.
    kind text NOT NULL,
    reason text NOT NULL,
    -- What the reason names, for a page to link to: the goal, the tasks holding the places, the shared paths.
    detail jsonb NOT NULL DEFAULT '{}',
    -- When the task began waiting for this reason, and when a pass last found it still waiting.
    since timestamptz NOT NULL DEFAULT now(),
    last_checked timestamptz NOT NULL DEFAULT now()
);
