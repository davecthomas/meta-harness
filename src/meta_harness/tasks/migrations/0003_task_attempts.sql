-- How many attempts a task's first run makes at once, for the operator to compare and pick from. 1 is the usual one.

ALTER TABLE task ADD COLUMN attempts integer NOT NULL DEFAULT 1;
