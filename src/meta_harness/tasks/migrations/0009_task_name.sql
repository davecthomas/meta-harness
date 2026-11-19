-- A task's short name: two to four words a person can say, shown wherever its id was. The planner writes it
-- after the task is created (planner/naming.py), so a task has none at first and a page shows its title until
-- then. No two tasks of one goal share a name.

ALTER TABLE task ADD COLUMN name text;
