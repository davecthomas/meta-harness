-- Whether the files the goal's runs create take a provenance header (run_service/provenance.py): true or
-- false outranks config/provenance.json; null leaves it to that file.
ALTER TABLE planner.goal ADD COLUMN IF NOT EXISTS provenance boolean;
