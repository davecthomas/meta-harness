-- The issue's title as the tracker had it when the planner took the issue in, refreshed by each sweep, so a
-- goal's board can name an issue that has no task yet. Null for a link stored before, until its next sweep.

ALTER TABLE issue_link ADD COLUMN title text;
