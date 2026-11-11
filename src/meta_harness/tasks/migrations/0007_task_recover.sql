-- What the supervisor needs of a task. status_changed_at: when the task last changed status, which
-- updated_at does not say (a held task's reason, a claim and a cancel request all move updated_at).
-- recoveries: how many times the supervisor found no workflow behind the task and sent it back to the queue.
-- spent_carried_usd: what the task's runs had spent when it was recovered, which its next workflow counts
-- against its spend limit; an operator's retry starts from nothing spent, and sets it to 0.

ALTER TABLE task ADD COLUMN status_changed_at timestamptz DEFAULT now();
UPDATE task SET status_changed_at = updated_at;
ALTER TABLE task ADD COLUMN recoveries integer NOT NULL DEFAULT 0;
ALTER TABLE task ADD COLUMN spent_carried_usd numeric(12, 4) NOT NULL DEFAULT 0;
