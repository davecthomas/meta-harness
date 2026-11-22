-- What an issue's work is, as a hash, so a goal can tell two issues that are the same work: the same title,
-- and either the same body or the same planning task as their source. On 2026-11-22 a plan's new tasks were
-- created three times over, and the goal built each of them two or three times. Null for a link from before
-- this, which is never taken for a copy.

ALTER TABLE issue_link ADD COLUMN content_hash text;
CREATE INDEX issue_link_goal_content ON issue_link (goal_id, content_hash);
