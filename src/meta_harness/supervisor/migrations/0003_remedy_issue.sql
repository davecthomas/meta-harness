-- A remedy may be for an issue with no task yet (the planner splitting a held issue): its row names the issue's
-- ref, and no task.

ALTER TABLE remedy ADD COLUMN ref text;
ALTER TABLE remedy ALTER COLUMN task_id DROP NOT NULL;
CREATE INDEX remedy_ref ON remedy (ref, created_at);
