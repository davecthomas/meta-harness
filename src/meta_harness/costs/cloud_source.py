"""Cloud cost, harvested from the Google Cloud billing export in BigQuery.

Google Cloud writes the project's bill to a BigQuery table once the billing account's export is turned on
(Billing, Billing export, Detailed or Standard usage cost). MH_BILLING_EXPORT_TABLE names the dataset the export
writes to, `<project>.<dataset>`, and the harvest finds the export's table in it: the detailed export's
`gcp_billing_export_resource_v1_<billing account>` when there is one, else the standard export's
`gcp_billing_export_v1_<billing account>`. So the billing account's id is named nowhere in the repo. It may name the table itself,
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

from meta_harness import retrying
import psycopg

from meta_harness.costs import store
from meta_harness.costs.store import Cost

SOURCE = "cloud"
SETTING = "MH_BILLING_EXPORT_TABLE"
TABLE = re.compile(r"^[A-Za-z0-9_\-]+\.[A-Za-z0-9_]+(\.[A-Za-z0-9_\-]+)?$")
# The export tables' names before the billing account's id, the one preferred first: the detailed usage cost
# export's, then the standard one's. Both have the columns the harvest reads.
EXPORT_PREFIXES = ("gcp_billing_export_resource_v1_", "gcp_billing_export_v1_")
# How far back a harvest reads again: Google restates a day's cost for a while after it. A harvest also reads
# from the first of the previous month, which is how far back Google fills a newly turned-on export.
DAYS = 35


def first_day(today: date) -> date:
    """The first day a harvest reads: DAYS ago, or the first of the previous month when that is earlier."""
    previous_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
    return min(today - timedelta(days=DAYS), previous_month)


METADATA_TOKEN = "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token"
QUERY_URL = "https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries"
TABLES_URL = "https://bigquery.googleapis.com/bigquery/v2/projects/{project}/datasets/{dataset}/tables"


class CloudCostError(RuntimeError):
    pass


class NoExportTable(CloudCostError):
    """The dataset holds no billing export table yet."""


def table() -> str | None:
    """The billing export's dataset or table, as the setting names it, or None when the deployment names none.

    Raises:
        CloudCostError: The setting is set to something that is not <project>.<dataset>[.<table>].
    """
    name = os.environ.get(SETTING, "").strip()
    if not name:
        return None
    if not TABLE.match(name):
        raise CloudCostError(f"{SETTING} must be <project>.<dataset> or <project>.<dataset>.<table>, not {name!r}")
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
    first = first_day(today)
    project = name.split(".", 1)[0]
    with httpx.Client(timeout=60, transport=retrying.transport(transport, safe=retrying.always)) as http:
        headers = {"Authorization": f"Bearer {_token(http)}"}
        if name.count(".") == 1:
            name = _export_table(http, headers, name)
        return _read(conn, http, headers, project, name, first, today)


def _token(http: httpx.Client) -> str:
    try:
        token = http.get(METADATA_TOKEN, headers={"Metadata-Flavor": "Google"})
    except httpx.HTTPError as err:
        raise CloudCostError(f"no Google credentials here (the metadata server is unreachable: {err})") from err
    if token.status_code != 200:
        raise CloudCostError(f"the metadata server gave no token ({token.status_code})")
    return token.json()["access_token"]


def _export_table(http: httpx.Client, headers: dict, dataset: str) -> str:
    """The export's table in the dataset: the detailed usage cost export's when there is one, else the standard's.

    Raises:
        NoExportTable: The dataset holds neither.
    """
    project, name = dataset.split(".")
    listed = http.get(TABLES_URL.format(project=project, dataset=name), headers=headers, params={"maxResults": 1000})
    if listed.status_code != 200:
        raise CloudCostError(f"BigQuery would not list {dataset}'s tables ({listed.status_code}): {listed.text[:300]}")
    tables = [t["tableReference"]["tableId"] for t in listed.json().get("tables") or []]
    for prefix in EXPORT_PREFIXES:
        found = sorted(t for t in tables if t.startswith(prefix))
        if found:
            return f"{dataset}.{found[0]}"
    raise NoExportTable(
        f"{dataset} has no billing export table yet: Google creates it within a day of the export being turned on"
    )


def _read(
    conn: psycopg.Connection, http: httpx.Client, headers: dict, project: str, name: str, first: date, today: date
) -> int:
    query = (
        "SELECT FORMAT_DATE('%F', DATE(usage_start_time)) AS day, service.description AS service, "
        "SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS usd "
        f"FROM `{name}` WHERE DATE(usage_start_time) >= '{first.isoformat()}' GROUP BY day, service"
    )
    response = http.post(
        QUERY_URL.format(project=project),
        json={"query": query, "useLegacySql": False, "timeoutMs": 50000, "maxResults": 10000},
        headers=headers,
    )
    if response.status_code != 200:
        raise CloudCostError(f"BigQuery answered {response.status_code}: {response.text[:300]}")
    body = response.json()
    if not body.get("jobComplete", True):
        raise CloudCostError("the billing query did not finish in time; the next read tries again")
    costs = []
    for row in body.get("rows") or []:
        day, service, usd = (cell.get("v") for cell in row["f"])
        if usd is None or not Decimal(str(usd)):
            continue
        costs.append(Cost(day=date.fromisoformat(day), source=SOURCE, item=service or "Other", usd=Decimal(str(usd))))
    store.replace(conn, SOURCE, first, today, costs)
    return len(costs)
