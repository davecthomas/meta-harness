-- The reason the planner last gave the issue's tracker, in a comment, for not starting the issue
-- (tracker_replies.py). Null: none given yet. A sweep comments only when the reason differs from this.
ALTER TABLE issue_link ADD COLUMN replied text;
