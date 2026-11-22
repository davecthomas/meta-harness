-- Created by the meta-harness in run t-6ba89f3bc769:run-1 on harness:pi-careful/claude:claude-opus-5-5.

-- Who an issue of the meta-harness's own tracker is assigned to (trackers/own.py, assign): a console user's
-- email. Null when no one is.
ALTER TABLE planner.own_issue ADD COLUMN IF NOT EXISTS assignee text;
