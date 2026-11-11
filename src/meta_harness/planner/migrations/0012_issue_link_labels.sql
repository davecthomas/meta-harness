-- The issue's labels as the tracker had them when the planner took the issue in, refreshed by each sweep, so
-- the Performance page can filter and break costs down by the labels of the issue a task worked on. Null for
-- a link stored before, until its next sweep; a cost reads its goal's label in their place till then.

ALTER TABLE issue_link ADD COLUMN labels text[];
