-- The operator a goal acts as on GitHub: its clones, pushes and pull requests use that operator's own
-- connection (planner.github_connection). Null: the goal uses the GitHub App, as every goal did before.
ALTER TABLE planner.goal ADD COLUMN acts_as text;
