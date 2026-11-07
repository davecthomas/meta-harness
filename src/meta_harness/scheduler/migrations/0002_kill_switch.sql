-- The kill switch: while it is on, the meta-harness scheduler admits nothing and stops every running task.

CREATE TABLE kill_switch (
    id integer PRIMARY KEY CHECK (id = 1),
    engaged boolean NOT NULL,
    changed_by text NOT NULL,
    reason text,
    changed_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO kill_switch (id, engaged, changed_by, reason) VALUES (1, false, 'migration', 'starts off');
