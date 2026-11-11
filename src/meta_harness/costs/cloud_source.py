"""Cloud cost, harvested from the Google Cloud billing export in BigQuery.

Google Cloud writes the project's bill to a BigQuery table once the billing account's export is turned on
(Billing, Billing export, Standard usage cost). MH_BILLING_EXPORT_TABLE names that table,
`<project>.<dataset>.<table>`. With it unset there is no cloud cost to read, and the cost API says so.

The service asks BigQuery for each day's cost by billed service (Compute Engine, Cloud Run, Cloud Logging and
so on), credits taken off, with its own service account through the metadata server's token: no key, no new
library. A bill is restated for days after the fact, so each harvest reads the last DAYS days again and puts
them in place of what it had.

Temporal has no bill of its own here: it runs on the VM, so its cost is inside Compute Engine's.
"""

import os
import re
from datetime import date, timedelta
from decimal import Decimal

import httpx
import psycopg

from meta_harness.costs import store
from meta_harness.costs.store import Cost

SOURCE = "cloud"
SETTING = "MH_BILLING_EXPORT_TABLE"
TABLE = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_\-]+$")
# How far back a harvest reads again: Google restates a day's cost for a while after it.
DAYS = 35
METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
QUERY_URL = "https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"


class CloudCostError(RuntimeError):
    pass


def table() -> str | None:
    """The billing export table, or None when the deployment names none.

    Raises:
        CloudCostError: The setting is set to something that is not <project>.<dataset>.<table>.
    """
    name = os.environ.get(SETTING, "").strip()
    if not name:
        return None
    if not TABLE.match(name):
        raise CloudCostError(f"{SETTING} must be <project>.<dataset>.<table>, not {name!r}")
    return name


def harvest(conn: psycopg.Connection, today: date, transport: httpx.BaseTransport | None = None) -> int:
    """Read the last DAYS days of the bill and put them in place. Returns how many rows it read, 0 when the
    deployment names no billing export.

    Raises:
        CloudCostError: BigQuery or the metadata server refused, with its reason.
    """
    name = table()
    if name is None:
        return 0
    first = today - timedelta(days=DAYS)
    project = name.split(".", 1)[0]
    query = (
        "SELECT FORMAT_DATE('%F', DATE(usage_start_time)) AS day, service.description AS service, "
        "SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS usd "
        f"FROM `{name}` WHERE DATE(usage_start_time) >= '{first.isoformat()}' GROUP BY day, service"
    )
    with httpx.Client(timeout=60, transport=transport) as http:
        try:
            token = http.get(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
        except httpx.HTTPError as err:
            raise CloudCostError(f"no Google credentials here (the metadata server is unreachable: {err})") from err
        if token.status_code != 200:
            raise CloudCostError(f"the metadata server gave no token ({token.status_code})")
        response = http.post(
            QUERY_URL.format(project=project),
            json={"query": query, "useLegacySql": False, "timeoutMs": 50000, "maxResults": 10000},
            headers={"Authorization": f"Bearer {token.json()['access_token']}"},
        )
    if response.status_code != 200:
        raise CloudCostError(f"BigQuery answered {response.status_code}: {response.text[:300]}")
    body = response.json()
    if not body.get("jobComplete", True):
        raise CloudCostError("the billing query did not finish in time; the next harvest asks again")
    costs = []
    for row in body.get("rows") or []:
        day, service, usd = (cell.get("v") for cell in row["f"])
        if usd is None or not Decimal(str(usd)):
            continue
        costs.append(Cost(day=date.fromisoformat(day), source=SOURCE, item=service or "Other", usd=Decimal(str(usd))))
    store.replace(conn, SOURCE, first, today, costs)
    return len(costs)
