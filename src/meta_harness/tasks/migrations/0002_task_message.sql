-- Operators' messages to running tasks, kept until the scheduler hands each to the task's RunTask workflow.

CREATE TABLE task_message (
    id bigserial PRIMARY KEY,
    task_id text NOT NULL REFERENCES task (id),
    body text NOT NULL,
    deliver text NOT NULL,
    actor text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);

CREATE INDEX task_message_pending ON task_message (id) WHERE delivered_at IS NULL;
