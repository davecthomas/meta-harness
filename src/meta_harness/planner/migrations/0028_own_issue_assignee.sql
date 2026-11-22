-- Created by the meta-harness in run t-dc988093ef68:run-1 on harness:pi-careful/claude:claude-opus-5-5.

-- Who an issue of the meta-harness's own tracker is assigned to (trackers/own.py): a console user's email,
-- empty when nobody is.
ALTER TABLE planner.own_issue ADD COLUMN IF NOT EXISTS assignee text NOT NULL DEFAULT '';
