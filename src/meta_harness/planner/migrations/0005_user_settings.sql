-- Each console user's own settings: planner defaults for New goal, appearance and notifications.

CREATE TABLE user_settings (
    identity text PRIMARY KEY,
    settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
