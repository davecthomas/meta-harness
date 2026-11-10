"""The VM's services' and the edge's logs, read from Cloud Logging for the console's Logs page.

The VM's Ops Agent ships the systemd journal as the log `mh_journald`
(deploy/vm/ops-agent.yaml); the edge's own output is Cloud Run's. The edge
reads them with its own service account, which holds roles/logging.viewer,
through the metadata server's token, so it needs no key and no new library.
"""

import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
ENTRIES_LIST = "https://logging.googleapis.com/v2/entries:list"
MAX_ENTRIES = 500
# The units whose logs the page offers, and the edge.
UNITS = (
    "mh-events",
    "mh-tasks",
    "mh-planner",
    "mh-scheduler",
    "mh-worker",
    "mh-gateway",
    "mh-egress",
    "mh-postgres",
    "mh-temporal",
    "mh-migrate",
    "mh-images",
)
EDGE = "edge"
SEVERITIES = ("DEFAULT", "DEBUG", "INFO", "NOTICE", "WARNING", "ERROR", "CRITICAL", "ALERT", "EMERGENCY")


class LogsError(RuntimeError):
    pass


def log_filter(project: str, unit: str | None, severity: str | None, since: datetime, q: str | None) -> str:
    """The Cloud Logging filter for one unit (or every one, and the edge), a lowest severity, a start and text."""
    vm = f'logName="projects/{project}/logs/mh_journald"'
    edge = 'resource.type="cloud_run_revision" AND resource.labels.service_name="mh-edge"'
    if unit == EDGE:
        source = edge
    elif unit:
        source = f'{vm} AND jsonPayload._SYSTEMD_UNIT="{unit}.service"'
    else:
        source = f'(({vm} AND jsonPayload._SYSTEMD_UNIT=~"^mh-") OR ({edge}))'
    parts = [source, f'timestamp>="{since.astimezone(timezone.utc).isoformat()}"']
    if severity:
        parts.append(f"severity>={severity}")
    if q:
        # Quotes and backslashes would end the search string early.
        parts.append(f'SEARCH("{re.sub(r"[\\\\\"]", " ", q)}")')
    return " AND ".join(parts)


def entry_row(entry: dict[str, Any]) -> dict[str, Any]:
    """One log entry as the page shows it: time, service, severity and message, with the whole entry."""
    payload = entry.get("jsonPayload") or {}
    unit = payload.get("_SYSTEMD_UNIT", "")
    service = unit.removesuffix(".service") if unit else EDGE
    message = payload.get("MESSAGE") or payload.get("message") or entry.get("textPayload") or ""
    return {
        "time": entry.get("timestamp"),
        "service": service,
        "severity": entry.get("severity", "DEFAULT"),
        "message": str(message),
        "entry": entry,
    }


async def read_logs(
    unit: str | None,
    severity: str | None,
    minutes: int,
    q: str | None,
    transport: httpx.AsyncBaseTransport | None = None,
    project: str | None = None,
) -> list[dict[str, Any]]:
    """Up to MAX_ENTRIES entries, newest first. Raises LogsError with Cloud Logging's or the metadata server's reason."""
    if unit and unit != EDGE and unit not in UNITS:
        raise LogsError(f"no unit {unit}: the units are {', '.join(UNITS)} and {EDGE}")
    if severity and severity not in SEVERITIES:
        raise LogsError(f"no severity {severity}: use one of {', '.join(SEVERITIES)}")
    project = project or os.environ.get("MH_PROJECT", "")
    if not project:
        raise LogsError("MH_PROJECT is not set here, so there is no project to read logs from")
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    async with httpx.AsyncClient(timeout=30, transport=transport) as http:
        try:
            token = await http.get(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
        except httpx.HTTPError as err:
            raise LogsError(f"no Google credentials here (the metadata server is unreachable: {err})") from err
        if token.status_code != 200:
            raise LogsError(f"the metadata server gave no token ({token.status_code})")
        body = {
            "resourceNames": [f"projects/{project}"],
            "filter": log_filter(project, unit, severity, since, q),
            "orderBy": "timestamp desc",
            "pageSize": MAX_ENTRIES,
        }
        response = await http.post(
            ENTRIES_LIST, json=body, headers={"Authorization": f"Bearer {token.json()['access_token']}"}
        )
    if response.status_code != 200:
        raise LogsError(f"Cloud Logging answered {response.status_code}: {response.text[:300]}")
    return [entry_row(e) for e in response.json().get("entries", [])]
