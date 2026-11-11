"""A time series of costs, as the Performance page draws it: buckets, one series per value, and the trend.

Nothing here reads anything: it turns each day's cost per value into buckets of a day, a week or a month, keeps
the most expensive values as series and folds the rest into "other", and fits a straight line through the buckets'
totals, so every client draws the same trend.
"""

from datetime import date, timedelta
from decimal import Decimal

BUCKETS = ("day", "week", "month")
# The most series a chart shows; the rest are one series, OTHER.
MAX_SERIES = 7
OTHER = "other"


def bucket_start(day: date, bucket: str) -> date:
    """The first day of the bucket a day falls in: itself, its Monday, or the first of its month."""
    if bucket == "week":
        return day - timedelta(days=day.weekday())
    if bucket == "month":
        return day.replace(day=1)
    return day


def buckets(first: date, last: date, bucket: str) -> list[date]:
    """Every bucket from first to last, with none left out: a bucket with no cost is a bar of zero."""
    found, day = [], bucket_start(first, bucket)
    while day <= last:
        found.append(day)
        if bucket == "month":
            day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            day += timedelta(days=7 if bucket == "week" else 1)
    return found


def trend(totals: list[float]) -> dict | None:
    """The least-squares line through the buckets' totals: its slope per bucket and its two ends. None with
    fewer than two buckets, where a line says nothing."""
    n = len(totals)
    if n < 2:
        return None
    mean_x, mean_y = (n - 1) / 2, sum(totals) / n
    spread = sum((x - mean_x) ** 2 for x in range(n))
    slope = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(totals)) / spread
    start = mean_y - slope * mean_x
    return {"slope": round(slope, 4), "start": round(start, 4), "end": round(start + slope * (n - 1), 4)}


def build(rows: list[tuple[date, str, Decimal]], first: date, last: date, bucket: str) -> dict:
    """The series for the rows (day, value, cost): the buckets, a series per value most expensive first, each
    bucket's total, and the trend through the totals."""
    starts = buckets(first, last, bucket)
    index = {start: n for n, start in enumerate(starts)}
    by_key: dict[str, list[Decimal]] = {}
    for day, key, usd in rows:
        by_key.setdefault(key or "", [Decimal(0)] * len(starts))[index[bucket_start(day, bucket)]] += usd
    ranked = sorted(by_key.items(), key=lambda item: sum(item[1]), reverse=True)
    kept, rest = ranked[:MAX_SERIES], ranked[MAX_SERIES:]
    if rest:
        folded = [sum((values[n] for _, values in rest), Decimal(0)) for n in range(len(starts))]
        kept.append((OTHER, folded))
    totals = [float(sum((values[n] for _, values in ranked), Decimal(0))) for n in range(len(starts))]
    return {
        "buckets": [start.isoformat() for start in starts],
        "series": [
            {"key": key, "usd": [round(float(v), 4) for v in values], "total": round(float(sum(values)), 4)}
            for key, values in kept
        ],
        "totals": [round(t, 4) for t in totals],
        "trend": trend(totals),
    }
