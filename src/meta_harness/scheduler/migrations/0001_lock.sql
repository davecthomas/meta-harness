-- Repo-area locks: one row per running task, held until the task's pull request opens or the task ends.
-- area is a path the task names, '' for a task that names none, or '*' (from before) for the whole repo. Redis replaces this table
-- when the meta-harness scheduler moves to its own deployment.

CREATE TABLE repo_area_lock (
    task_id text NOT NULL,
    repo text NOT NULL,
    area text NOT NULL,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (task_id, area)
);

CREATE INDEX repo_area_lock_repo ON repo_area_lock (repo);
