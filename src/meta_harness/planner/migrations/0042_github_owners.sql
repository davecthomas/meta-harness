-- The GitHub accounts and organizations an operator wants Muxxy to use, of those their connection can reach.
-- Empty: all of them.
ALTER TABLE planner.github_connection ADD COLUMN owners text[] NOT NULL DEFAULT '{}';
