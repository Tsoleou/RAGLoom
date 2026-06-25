"""Dashboard endpoints: aggregate query-behavior analytics + recent history.

Read-only over the query log (api/query_log.py). All heavy lifting (grouping,
percentiles, knowledge-gap detection) happens in SQL inside query_log; these
endpoints are thin pass-throughs so the route table stays obvious.
"""

import csv
import io

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from api.query_log import EXPORT_COLUMNS, fetch_all, fetch_recent, fetch_stats

router = APIRouter()


@router.get("/api/dashboard/stats")
def dashboard_stats(days: int = 7):
    """Aggregated analytics over the last `days` days (days<=0 = all time)."""
    return fetch_stats(days=days)


@router.get("/api/dashboard/queries")
def dashboard_queries(limit: int = 50, offset: int = 0):
    """Most-recent query rows for the history table."""
    return {"queries": fetch_recent(limit=min(limit, 500), offset=offset)}


# Leading characters that spreadsheet apps treat as the start of a formula.
# A cell beginning with one of these (e.g. a user query like `=cmd|...`) can be
# executed on open, so we neutralise it by prefixing a single quote.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(value):
    """Defang CSV formula injection in string cells (non-strings pass through)."""
    if isinstance(value, str) and value.startswith(_CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


@router.get("/api/dashboard/export.csv")
def dashboard_export_csv(days: int = 0):
    """Download the full query history for the range as a CSV attachment.

    Unlike the paginated `/queries` table this exports every row in the window
    (days<=0 = all time). A UTF-8 BOM is prepended so spreadsheet apps render
    the CJK queries correctly, and string cells are defanged against CSV
    formula injection.
    """

    def stream():
        # Serialise row-by-row through one recycled buffer so we don't also hold
        # the full concatenated CSV string in memory on top of the row list.
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")

        buf.write("﻿")  # BOM so Excel reads the CJK columns as UTF-8
        writer.writeheader()
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate(0)

        for row in fetch_all(days=days):
            writer.writerow({k: _csv_safe(v) for k, v in row.items()})
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)

    suffix = "all" if days <= 0 else f"{days}d"
    return StreamingResponse(
        stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="query_history_{suffix}.csv"'
        },
    )
