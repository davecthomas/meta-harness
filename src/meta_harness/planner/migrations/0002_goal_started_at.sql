-- When a goal first became active: null for a goal saved without starting, until its first resume.
-- The default stamps a goal that code from before this column creates active; every goal so far started
-- when it was created.

ALTER TABLE goal ADD COLUMN started_at timestamptz DEFAULT now();
UPDATE goal SET started_at = created_at;
