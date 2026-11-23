-- The plan version an update was written about. A planning goal's page compares it with the plan's version
-- now: an update about a version since replaced is shown as that, and asks nothing of the operator.

ALTER TABLE goal_update ADD COLUMN plan_version integer;
