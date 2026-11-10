"""Each console user's own settings, keyed by the signed-in identity the edge names in X-MH-Actor.

The planner keeps them because the New goal form starts from the planner
defaults among them. A user reads and writes only their own row.
"""

from decimal import Decimal
from typing import Literal

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field


class PlannerDefaults(BaseModel):
    """What the New goal form starts from."""

    model_config = ConfigDict(extra="forbid")
    repos: list[str] = Field(default_factory=list)
    modality: Literal["fix", "feature", "new-system"] = "fix"
    latitude: float | None = Field(default=None, ge=0, le=1)
    max_usd: Decimal | None = Field(default=None, gt=0)
    auto_merge: bool = False


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planner: PlannerDefaults = Field(default_factory=PlannerDefaults)
    appearance: Literal["light", "dark", "system"] = "system"
    # Browser notifications for new inbox items, once the operator allows them in the browser.
    notifications: bool = False


def read(conn: psycopg.Connection, identity: str) -> UserSettings:
    row = conn.execute("SELECT settings FROM planner.user_settings WHERE identity = %s", (identity,)).fetchone()
    return UserSettings.model_validate(row[0]) if row else UserSettings()


def write(conn: psycopg.Connection, identity: str, settings: UserSettings) -> UserSettings:
    with conn.transaction():
        conn.execute(
            "INSERT INTO planner.user_settings (identity, settings) VALUES (%s, %s) "
            "ON CONFLICT (identity) DO UPDATE SET settings = EXCLUDED.settings, updated_at = now()",
            (identity, Jsonb(settings.model_dump(mode="json"))),
        )
    return settings
