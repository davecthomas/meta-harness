-- Whether an operator picked the issue for its goal by ref, rather than the goal's label sweeping it in.
-- A goal's label may be cleared only when it holds picked issues. A goal with no label has only picked ones;
-- an older link on a labeled goal cannot be told apart, so it counts as swept.

ALTER TABLE issue_link ADD COLUMN picked boolean NOT NULL DEFAULT false;
UPDATE issue_link l SET picked = true FROM goal g WHERE g.id = l.goal_id AND g.label IS NULL;
