-- The issue tracker the goal uses (trackers/registry.py): one per goal, set when it is created and kept.
-- Null for a goal from before goals named one, which uses GitHub.
ALTER TABLE goal ADD COLUMN tracker text;
