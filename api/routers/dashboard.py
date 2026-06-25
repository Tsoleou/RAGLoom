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


@router.get("/api/dashboard/export.csv")
def dashboard_export_csv(days: int = 0):
    """Download the full query history for the range as a CSV attachment.

    Unlike the paginated `/queries` table this exports every row in the window
    (days<=0 = all time). A UTF-8 BOM is prepended so spreadsheet apps render
    the CJK queries correctly.
    """
    rows = fetch_all(days=days)

    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel reads the CJK columns as UTF-8
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)

    suffix = "all" if days <= 0 else f"{days}d"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="query_history_{suffix}.csv"'
        },
    )
