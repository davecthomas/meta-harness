-- A narrative summary of a goal's work, written for one kind of reader (a persona), and one of all running
-- goals for the Overview. Each is kept with the digest of the facts it was written from: it is read from here
-- until those facts change, and only then written again. scope is a goal's id, or 'overview'.

CREATE TABLE narrative (
    scope text NOT NULL,
    persona text NOT NULL,
    digest text NOT NULL,
    body text NOT NULL,
    model text NOT NULL,
    usd_cost numeric(12, 6) NOT NULL DEFAULT 0,
    written_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, persona)
);

-- What narratives cost each day (UTC), against the planning configuration's narratives.max_usd.
CREATE TABLE narrative_spend (
    day date PRIMARY KEY,
    usd numeric(12, 6) NOT NULL DEFAULT 0
);
