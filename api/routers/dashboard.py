"""Dashboard endpoints: aggregate query-behavior analytics + recent history.

Read-only over the query log (api/query_log.py). All heavy lifting (grouping,
percentiles, knowledge-gap detection) happens in SQL inside query_log; these
endpoints are thin pass-throughs so the route table stays obvious.
"""

from fastapi import APIRouter

from api.query_log import fetch_recent, fetch_stats

router = APIRouter()


@router.get("/api/dashboard/stats")
def dashboard_stats(days: int = 7):
    """Aggregated analytics over the last `days` days (days<=0 = all time)."""
    return fetch_stats(days=days)


@router.get("/api/dashboard/queries")
def dashboard_queries(limit: int = 50, offset: int = 0):
    """Most-recent query rows for the history table."""
    return {"queries": fetch_recent(limit=min(limit, 500), offset=offset)}
