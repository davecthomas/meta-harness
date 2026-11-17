-- What the goal told its tracker besides its repos (trackers/base.py, Source): for Jira, the project its issues
-- are in. Null for a goal whose tracker asks nothing of it.
ALTER TABLE goal ADD COLUMN tracker_settings jsonb;
