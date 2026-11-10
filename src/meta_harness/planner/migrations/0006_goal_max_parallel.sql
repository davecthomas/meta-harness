-- The most of a goal's tasks that run at once. Null: the task policy's max_parallel, the system's.

ALTER TABLE goal ADD COLUMN max_parallel integer;
