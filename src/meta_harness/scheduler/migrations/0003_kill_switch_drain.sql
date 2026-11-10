-- Draining: the scheduler admits no new task, and running tasks finish. A deploy that replaces the VM drains
-- first, and the new VM's boot releases it. Separate from engaged, which also stops running tasks.

ALTER TABLE kill_switch ADD COLUMN draining boolean NOT NULL DEFAULT false;
