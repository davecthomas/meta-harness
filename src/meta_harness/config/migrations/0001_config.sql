-- Configuration documents an operator edits in the console: every saved version of each kind, and
-- which version is active. Nothing is ever overwritten, so any earlier version can be made active again.

CREATE TABLE config_version (
    kind text NOT NULL,
    version integer NOT NULL,
    document jsonb NOT NULL,
    edited_by text NOT NULL,
    note text,
    -- seed (from the deployment's files), edit, or rollback (of an earlier version).
    origin text NOT NULL,
    rolled_back_from integer,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kind, version)
);

CREATE TABLE active (
    kind text PRIMARY KEY,
    version integer NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (kind, version) REFERENCES config_version (kind, version)
);
