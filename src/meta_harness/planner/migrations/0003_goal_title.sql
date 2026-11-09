-- A goal's title: a short name for lists, beside its statement, which the console shows as its description.
-- Null until the operator or the planner's model gives it one.

ALTER TABLE goal ADD COLUMN title text;
