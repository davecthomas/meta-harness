-- The repos an operator's GitHub connection reached at its last read, and when. Held in memory before, so every
-- restart of the planner (each deploy) made the next New goal wait on GitHub to list them all again.
ALTER TABLE planner.github_connection ADD COLUMN reached_repos jsonb NOT NULL DEFAULT '[]';
ALTER TABLE planner.github_connection ADD COLUMN reached_at timestamptz;
