-- Created by the meta-harness in run t-4d0438b23b7a:run-1 on harness:pi-careful/claude:claude-opus-5-5.

-- The open questions (gaps) an operator said are not a problem, on the plan version they were dismissed from:
-- each is {gap, by, at}. The gap itself leaves that version's gaps, so a reload leaves it out.
ALTER TABLE planner.plan ADD COLUMN dismissed_gaps jsonb NOT NULL DEFAULT '[]';
