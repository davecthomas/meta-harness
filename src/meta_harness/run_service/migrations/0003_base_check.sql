-- How each of a repo's standing checks ended on one commit of its base branch, in the sandbox a task runs in.
-- A check runs once for a commit: a task that finds its result here reads it and runs nothing. A row is
-- 'running' while one task's base check holds it, then 'passed' or 'failed' with the end of its output.

CREATE TABLE base_check (
    repo text NOT NULL,
    commit_sha text NOT NULL,
    command text NOT NULL,
    state text NOT NULL DEFAULT 'running',
    exit_code integer,
    output text NOT NULL DEFAULT '',
    task_id text NOT NULL DEFAULT '',
    claimed_at timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz,
    PRIMARY KEY (repo, commit_sha, command)
);
