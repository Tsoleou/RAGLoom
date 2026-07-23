"""Dashboard endpoints: aggregate query-behavior analytics + recent history.

Read-only over the query log (api/query_log.py). All heavy lifting (grouping,
percentiles, knowledge-gap detection) happens in SQL inside query_log; these
endpoints are thin pass-throughs so the route table stays obvious.
"""

import csv
import io
import os
import sqlite3
import tempfile

from fastapi import APIRouter
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from api.query_log import (
    EXPORT_COLUMNS,
    fetch_all,
    fetch_recent,
    fetch_stats,
    run_readonly_sql,
)
from api.schemas import SqlQueryRequest

router = APIRouter()


@router.get("/api/dashboard/stats")
def dashboard_stats(days: int = 7):
    """Aggregated analytics over the last `days` days (days<=0 = all time)."""
    return fetch_stats(days=days)


@router.post("/api/dashboard/sql")
def dashboard_sql(req: SqlQueryRequest):
    """Run a read-only SELECT/WITH for ad-hoc diagnosis.

    Executes against a decrypted in-memory snapshot (table `queries`, columns =
    EXPORT_COLUMNS) so it can filter on the otherwise-encrypted question/answer
    text without ever touching the real store. Admin-gated by the middleware.
    Returns {columns, rows, row_count, truncated} or {error} on a bad query."""
    return run_readonly_sql(req.sql)


@router.get("/api/dashboard/queries")
def dashboard_queries(limit: int = 50, offset: int = 0, search: str = ""):
    """Most-recent query rows for the history table, optionally text-filtered.

    `search` matches question / answer / product / intent / status (case-
    insensitive) — see fetch_recent for why filtering happens in Python."""
    return {"queries": fetch_recent(limit=min(limit, 500), offset=offset, search=search)}


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


@router.get("/api/dashboard/export.db")
def dashboard_export_db(days: int = 0):
    """Download the query history as a clean, DECRYPTED SQLite database.

    The live store (data/queries.db) keeps question/answer free text encrypted
    at rest, so it can't be queried with plain SQL. This export writes one
    `queries` table (columns = EXPORT_COLUMNS) with the text decrypted, so it
    opens directly in DB Browser / DBeaver / the sqlite3 CLI for ad-hoc SQL.

    Built in a temp file that's removed after the response is sent. Columns are
    declared without a type (BLOB affinity) so numbers keep their numeric type
    instead of being coerced to text.
    """
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    try:
        cols_ddl = ", ".join(f'"{c}"' for c in EXPORT_COLUMNS)
        conn.execute(f"CREATE TABLE queries ({cols_ddl})")
        placeholders = ",".join("?" for _ in EXPORT_COLUMNS)
        conn.executemany(
            f"INSERT INTO queries VALUES ({placeholders})",
            ([row.get(c) for c in EXPORT_COLUMNS] for row in fetch_all(days=days)),
        )
        conn.commit()
    finally:
        conn.close()

    suffix = "all" if days <= 0 else f"{days}d"
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"query_history_{suffix}.db",
        background=BackgroundTask(os.remove, path),
    )
