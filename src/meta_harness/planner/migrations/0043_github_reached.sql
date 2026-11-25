-- The accounts and organizations an operator's GitHub connection reached when its repos were last read, each
-- with its repo count. Kept so Settings can list them at once, while the repos are read again behind it.
ALTER TABLE planner.github_connection ADD COLUMN reached jsonb NOT NULL DEFAULT '[]';
