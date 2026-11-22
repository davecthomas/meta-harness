-- What an outsider's issue said when an operator read it and let it run (picked it, or ran it anyway), as a
-- hash of its title and whole body. The author can edit their own issue afterwards, and the new text used to
-- be taken with no further look when its task was made over, split, or planned again. Null: not recorded yet,
-- which the next sweep does.

ALTER TABLE issue_link ADD COLUMN approved_hash text;
