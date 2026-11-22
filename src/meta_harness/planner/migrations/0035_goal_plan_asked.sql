-- When the goal's planning task in flight, or its last one, was asked for. The plan page shows how long the
-- planner has been planning, and then how long the plan took and how long it has waited for its approval:
-- time the operator otherwise sees as nothing happening.

ALTER TABLE goal ADD COLUMN plan_asked_at timestamptz;
