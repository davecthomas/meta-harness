-- A feature goal's plan: the issues a planning task drafted, held for an operator to approve before any work.
-- Each save is a new version: version 1 from the planning task, later ones from an operator's edit or a replan.
-- drafts is a list of {draft_id, repo, title, body, labels, depends_on}. invalid lists why the plan cannot be
-- approved; empty when it can. created maps each draft_id to the issue the planner created for it, filled as
-- an approval goes, so an approval GitHub interrupted carries on where it stopped.

CREATE TABLE plan (
    goal_id text NOT NULL REFERENCES goal (id),
    version integer NOT NULL,
    drafts jsonb NOT NULL,
    invalid jsonb NOT NULL DEFAULT '[]',
    source text NOT NULL,
    task_id text,
    note text,
    created jsonb NOT NULL DEFAULT '{}',
    created_by text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (goal_id, version)
);

-- The planning task in flight for the goal, when there is one, and when its plan was approved.
ALTER TABLE goal ADD COLUMN plan_task_id text;
ALTER TABLE goal ADD COLUMN plan_approved_at timestamptz;
