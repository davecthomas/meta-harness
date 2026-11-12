-- How many times the supervisor sent a parked task back for its pull request's merge, or back through the
-- review loop, since an operator last retried it. It bounds what the supervisor does by itself: past the
-- limit the task waits for an operator, who is told how often it was tried.

ALTER TABLE task ADD COLUMN drives integer NOT NULL DEFAULT 0;
