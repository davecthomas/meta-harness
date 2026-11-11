-- Who may use the console, and as what. The edge asks the planner, which keeps these beside each user's settings.
-- Before this table the access list was two environment variables on the edge, and changing it was a deploy.

CREATE TABLE console_user (
    email text PRIMARY KEY,
    -- admin, or user: a regular user, who changes no configuration and opens no admin page.
    role text NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    -- invited: may sign in, and has not yet. active: has signed in. deprovisioned: may not.
    status text NOT NULL DEFAULT 'invited' CHECK (status IN ('invited', 'active', 'deprovisioned')),
    invited_by text NOT NULL,
    invited_at timestamptz NOT NULL DEFAULT now(),
    last_sign_in_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
