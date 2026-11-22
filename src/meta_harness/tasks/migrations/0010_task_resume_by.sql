-- Who sent a parked task back to the queue: 'operator' for a person's merge, retry or start-over, 'supervisor'
-- for the Task Shepherd's own. A merge only an operator may ask for (a pull request that changes a path which
-- runs with the control plane's authority) reads it, so the supervisor's merge never lifts that hold.

ALTER TABLE task ADD COLUMN resume_by text;
