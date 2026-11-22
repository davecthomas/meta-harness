-- One approval of a goal's plan at a time. An approval writes each of the plan's new tasks to the tracker,
-- which takes longer than a browser waits, and an operator who saw no answer pressed Approve again: every
-- press started another approval, and each created all the issues. approving_at is the claim the running
-- approval holds; a second one is refused while it stands. approve_error keeps why the last approval stopped,
-- for the page of an operator whose own request had already timed out.

ALTER TABLE goal ADD COLUMN approving_at timestamptz;
ALTER TABLE goal ADD COLUMN approve_error text;
