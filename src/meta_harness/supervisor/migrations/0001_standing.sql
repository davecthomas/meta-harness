-- Who has each open task: one row per task that has not ended closed, written by the supervisor each pass.
-- A task with no row has not been looked at yet. The Task API serves a task's row as its `standing`.

CREATE TABLE standing (
    task_id text PRIMARY KEY,
    -- Who the task waits on: workflow, scheduler, planner, supervisor or operator.
    owner text NOT NULL,
    -- Which of the supervisor's situations the task is in: the rule that decided the owner.
    situation text NOT NULL,
    -- Why, in a sentence, and for an operator what to do about it.
    reason text NOT NULL,
    ask text,
    -- When the task entered this situation, and when a pass last found it there.
    since timestamptz NOT NULL DEFAULT now(),
    checked_at timestamptz NOT NULL DEFAULT now(),
    -- How many times the supervisor acted on the task in this situation, when it last did, and that action's
    -- error, if it failed.
    attempts integer NOT NULL DEFAULT 0,
    acted_at timestamptz,
    last_error text
);
