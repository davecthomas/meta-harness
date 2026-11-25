-- An operator's own GitHub connection: the account they signed in to GitHub with from the console, so a goal can
-- act with what that operator can already reach there, with no installation by an org owner and no repo
-- registered by an admin (docs/specs/goals-span-repos.md).
--
-- token is the access token, encrypted with the deployment's token key (MH_GITHUB_TOKEN_KEY): never in the
-- clear here, in a log or in an event. nonce is the one-time value a sign-in in flight must come back with.

CREATE TABLE github_connection (
    -- The console identity: the operator's verified email, as the edge names it.
    identity text PRIMARY KEY,
    login text,
    token text,
    scopes text NOT NULL DEFAULT '',
    -- pending (a sign-in is in flight), connected, or broken (GitHub refused the token: reconnect).
    state text NOT NULL DEFAULT 'pending',
    nonce text,
    connected_at timestamptz,
    checked_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);
