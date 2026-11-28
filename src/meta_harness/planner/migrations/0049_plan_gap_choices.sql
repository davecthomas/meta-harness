-- The answers a planner offers for one of a plan version's open questions (gaps), by the gap's text: two to four
-- short ones, the first the one the plan takes now. The plan's page draws each as a button, so an operator
-- answers a question that is a choice with one press.
ALTER TABLE planner.plan ADD COLUMN gap_choices jsonb NOT NULL DEFAULT '{}';
