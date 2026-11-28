-- The code host's security alerts the planner has filed as issues, so each is filed once: a code scanning or
-- dependency alert by its repo, kind and number, with the issue it became.

CREATE TABLE planner.security_alert (
    repo text NOT NULL,
    kind text NOT NULL,
    number integer NOT NULL,
    severity text NOT NULL DEFAULT '',
    ref text NOT NULL DEFAULT '',
    filed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo, kind, number)
);
