-- The issue the planner filed for a merged pull request's follow-up findings (planner/follow_ups.py): its ref,
-- or '' when the pull request left none to file. Null until the sweep has looked.
ALTER TABLE planner.issue_link ADD COLUMN IF NOT EXISTS follow_ups_ref text;
