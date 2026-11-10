-- Whether an operator picked the issue for its goal by ref, rather than the goal's label sweeping it in.
-- A goal's label may be cleared only when it holds picked issues. A goal with no label has only picked ones.
-- On a labeled goal, an older link is picked when the planner recorded it as issue_added for that goal and
-- did not drop it after: a link dropped and held again since came back by the label's sweep. Any other
-- older link counts as swept.

ALTER TABLE issue_link ADD COLUMN picked boolean NOT NULL DEFAULT false;
UPDATE issue_link l SET picked = true FROM goal g WHERE g.id = l.goal_id AND g.label IS NULL;

DO $$
BEGIN
    IF to_regclass('events.event') IS NOT NULL AND has_table_privilege('events.event', 'SELECT') THEN
        UPDATE issue_link l SET picked = true
        WHERE NOT l.picked
          AND EXISTS (
              SELECT 1 FROM events.event a
              WHERE a.event_type = 'issue_added'
                AND a.payload ->> 'ref' = l.ref
                AND a.payload ->> 'goal_id' = l.goal_id
                AND NOT EXISTS (
                    SELECT 1 FROM events.event d
                    WHERE d.event_type = 'issue_dropped'
                      AND d.payload ->> 'ref' = l.ref
                      AND d.payload ->> 'goal_id' = l.goal_id
                      AND d.position > a.position
                )
          );
    END IF;
END
$$;
