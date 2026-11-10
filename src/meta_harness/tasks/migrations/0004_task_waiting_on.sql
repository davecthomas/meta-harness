-- What a running task is waiting on an operator for, when it is: 'budget' while its workflow holds for a
-- larger spend limit or its goal's budget. Null otherwise. The task stays running: its workflow is alive.

ALTER TABLE task ADD COLUMN waiting_on text;
