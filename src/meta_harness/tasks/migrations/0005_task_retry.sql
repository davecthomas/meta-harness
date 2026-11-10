-- A task an operator sent back to the queue: how many times, and where its next RunTask workflow picks up.
-- resume is 'review' (its open pull request goes back through checks, review and fixes), 'merge' (an operator
-- asked for the merge) or null (a new first run).

ALTER TABLE task ADD COLUMN retries integer NOT NULL DEFAULT 0;
ALTER TABLE task ADD COLUMN resume text;
