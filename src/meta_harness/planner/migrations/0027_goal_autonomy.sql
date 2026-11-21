-- How much the meta-harness does for the goal's tasks without asking (planner/store.py, AUTONOMY): ask,
-- standard or full. Null for a goal made before the setting: full when its auto_merge is on, else standard.
ALTER TABLE planner.goal ADD COLUMN IF NOT EXISTS autonomy text;
