-- An issue is sized before it becomes a task (sizing.py), and a large one, or one missing what a strict goal
-- asks for, is held for an operator instead.

-- true: an issue missing acceptance checks, paths or a testable outcome is held, not run with the gap shown.
ALTER TABLE goal ADD COLUMN strict_issues boolean NOT NULL DEFAULT false;

-- The size the planner gave the issue (small, medium, large), null until it is sized, and what the sizing
-- found: its reasons, what the issue is missing, the suggested split, the issue's paths and checks for the split
-- drafts, and the model with its cost.
ALTER TABLE issue_link ADD COLUMN size text;
ALTER TABLE issue_link ADD COLUMN sizing jsonb;
-- Why the issue is held rather than made a task; null when it is not held.
ALTER TABLE issue_link ADD COLUMN held_reason text;
-- An operator chose Run anyway: the issue becomes a task whatever its size and gaps.
ALTER TABLE issue_link ADD COLUMN run_anyway boolean NOT NULL DEFAULT false;
