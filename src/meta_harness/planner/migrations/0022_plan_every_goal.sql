-- A plan for every goal (plans.py). Beside its new tasks (drafts), a plan holds the planner's decision on each
-- issue the goal already had (items), its summary, the gaps it found, and the issues it was made over.
ALTER TABLE planner.plan ADD COLUMN IF NOT EXISTS items jsonb NOT NULL DEFAULT '[]';
ALTER TABLE planner.plan ADD COLUMN IF NOT EXISTS summary text NOT NULL DEFAULT '';
ALTER TABLE planner.plan ADD COLUMN IF NOT EXISTS gaps jsonb NOT NULL DEFAULT '[]';
ALTER TABLE planner.plan ADD COLUMN IF NOT EXISTS issues jsonb NOT NULL DEFAULT '[]';
-- Whether an approval put the plan's new tasks in the goal's tracker (true) or kept them in the meta-harness.
ALTER TABLE planner.plan ADD COLUMN IF NOT EXISTS tracked boolean;

-- Whether the goal is planned before its tasks run. Every feature goal already was.
ALTER TABLE planner.goal ADD COLUMN IF NOT EXISTS planned boolean NOT NULL DEFAULT false;
UPDATE planner.goal SET planned = true WHERE modality = 'feature' AND NOT planned;
-- The issues the goal's planning task in flight was given, for its plan to be checked against.
ALTER TABLE planner.goal ADD COLUMN IF NOT EXISTS plan_issues jsonb;

-- When an approved plan, or an operator after it, took the issue into the goal: such an issue is not held
-- again for its size. And the issues the plan says it waits on, beside those its own text names.
ALTER TABLE planner.issue_link ADD COLUMN IF NOT EXISTS planned_at timestamptz;
ALTER TABLE planner.issue_link ADD COLUMN IF NOT EXISTS plan_depends_on text[] NOT NULL DEFAULT '{}';
