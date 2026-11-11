-- Each task whose pull request's end, merged or closed without merging, a sweep has recorded as an event, so a
-- sweep reads the pull request of a canceled task only until it ends: an operator can cancel a task and leave
-- its pull request open, to merge or close by hand later, and no workflow watches it then.

CREATE TABLE pull_request_end (
    task_id text PRIMARY KEY,
    outcome text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
