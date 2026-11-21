-- What the supervisor offers an operator for a task, best first (supervisor/moves.py), and its account of the
-- task's reviews. Both are null for a task that waits on no one's decision, and for a row written before.

ALTER TABLE standing ADD COLUMN moves jsonb;
ALTER TABLE standing ADD COLUMN saw text;
