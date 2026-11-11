-- What a goal's budget does when it is reached, and how far past it a blocking goal may go.

-- block: the goal stops spending at its blocking line. warn: it keeps going, and reaching the budget is recorded.
ALTER TABLE goal ADD COLUMN budget_mode text NOT NULL DEFAULT 'block' CHECK (budget_mode IN ('block', 'warn'));
-- How far past max_usd a block goal may go before it stops, as a percent of max_usd.
ALTER TABLE goal ADD COLUMN budget_overage_percent integer NOT NULL DEFAULT 10 CHECK (budget_overage_percent >= 0);
-- What the goal's runs had spent when its last sweep looked.
ALTER TABLE goal ADD COLUMN spent_usd numeric(12, 4) NOT NULL DEFAULT 0;
-- The budget goal_budget_reached was last recorded for. Null, or another budget: it is recorded again.
ALTER TABLE goal ADD COLUMN budget_reached_usd numeric(12, 4);
-- The budget a resume would set, worked out by the last sweep of a goal past its budget.
ALTER TABLE goal ADD COLUMN budget_resume_to numeric(12, 4);
