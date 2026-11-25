-- A planning run that revises a plan starts from that plan's version: the goal keeps which, so the run's result
-- is that version's tasks with the run's changes over them, and a task the run did not name is unchanged.
-- A plan saved from a run that ended at a limit says which limit (turns, time or spend): its tasks are kept, and
-- the next run carries on from them.

ALTER TABLE goal ADD COLUMN plan_base_version integer;
ALTER TABLE plan ADD COLUMN stopped_at text;
