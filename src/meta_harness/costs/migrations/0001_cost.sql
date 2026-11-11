-- What the meta-harness cost, by day and by what the money went to. One row per day, source, item and the
-- dimensions that source has; a dimension a source lacks is ''.

CREATE TABLE daily_cost (
    day date NOT NULL,
    -- model: what model calls cost. cloud: the Google Cloud bill.
    source text NOT NULL,
    -- Within the source. model: the kind of run (task, review, fix, planning). cloud: the billed service.
    item text NOT NULL,
    goal_id text NOT NULL DEFAULT '',
    task_id text NOT NULL DEFAULT '',
    repo text NOT NULL DEFAULT '',
    model text NOT NULL DEFAULT '',
    engine text NOT NULL DEFAULT '',
    -- Who the work was for: the operator who created the task's goal.
    operator text NOT NULL DEFAULT '',
    usd numeric(14, 6) NOT NULL,
    PRIMARY KEY (day, source, item, goal_id, task_id, repo, model, engine, operator)
);

CREATE INDEX daily_cost_day ON daily_cost (day);

-- How far each source has been harvested: an event position for model, nothing for a source read whole.
CREATE TABLE harvest (
    source text PRIMARY KEY,
    position bigint NOT NULL DEFAULT 0,
    harvested_at timestamptz NOT NULL DEFAULT now(),
    -- Why the last harvest failed; null when it did not.
    error text
);
