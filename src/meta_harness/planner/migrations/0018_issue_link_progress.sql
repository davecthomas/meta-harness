-- What the planner last told the issue's tracker about its task (tracker_progress.py): the status it set, with the pull
-- request once there is one. Null: nothing told yet. A sweep writes to the tracker only when this would change.
ALTER TABLE issue_link ADD COLUMN progress text;
