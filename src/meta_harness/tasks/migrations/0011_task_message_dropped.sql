-- When a message was given up on: its task was no longer running, or had left the run the message was written
-- for. A dropped message is never handed to a workflow, so one written for a run that ended cannot reach a
-- later run of the same task. Nullable, and read by nothing older: code from before it ignores it.
ALTER TABLE task_message ADD COLUMN dropped_at timestamptz;
DROP INDEX task_message_pending;
CREATE INDEX task_message_pending ON task_message (id) WHERE delivered_at IS NULL AND dropped_at IS NULL;
