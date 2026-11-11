-- The operator who took a task's review over: the review round waits for their verdict. Null: no one has.

ALTER TABLE task ADD COLUMN review_claimed_by text;
