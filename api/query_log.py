"""Query behavior logging + analytics store.

A single denormalized SQLite table records one row per chat turn: the question,
how the pipeline answered it (ok / blocked / error), which guard fired, a
rule-based intent label, retrieval-quality summary, and end-to-end latency.

Design notes:
  - SQLite single file under `data/` (created on demand; not version-controlled).
  - One connection per call — FastAPI runs sync endpoints in a threadpool, so a
    module-level connection would trip `check_same_thread`. Per-call connect is
    cheap at this volume and sidesteps the issue entirely.
  - `log_query` NEVER raises: a logging failure must not break the chat path.
  - Intent is classified in code, not via the LLM — gemma3:4b can't be trusted
    with even classification-style judgments (see numeric-blindness findings),
    and rule-based keeps the dashboard deterministic and free.
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

from api.product_catalog import display_name

# DB lives next to the repo root under data/. Override with RAG_QUERY_LOG_DB.
_DB_PATH = os.environ.get("RAG_QUERY_LOG_DB", "./data/queries.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT    NOT NULL,          -- ISO-8601 UTC
    query         TEXT    NOT NULL,
    profile       TEXT,
    model         TEXT,
    latency_ms    INTEGER,
    status        TEXT,                      -- ok | blocked | error
    blocked       INTEGER DEFAULT 0,         -- 0/1
    blocked_reason TEXT,
    gate          TEXT,                      -- which guard fired (guardrail/price_guard/scope_gate)
    intent        TEXT,                      -- rule-based label
    product       TEXT,                      -- primary product_id asked about (top retrieved)
    top_score     REAL,
    avg_score     REAL,
    n_retrieved   INTEGER DEFAULT 0,
    n_passed      INTEGER DEFAULT 0,
    top_source    TEXT,
    rerank_kept   INTEGER,
    rerank_total  INTEGER,
    critic_verdict TEXT,
    critic_revised INTEGER,
    detail        TEXT                       -- raw retrieval/guards JSON for drill-down
);
CREATE INDEX IF NOT EXISTS idx_queries_ts ON queries(ts);
CREATE INDEX IF NOT EXISTS idx_queries_intent ON queries(intent);
"""

# Columns added after the first release — applied to pre-existing DBs on connect.
# (kept out of _SCHEMA so the index below runs only after the column exists.)
_MIGRATIONS = [("product", "ALTER TABLE queries ADD COLUMN product TEXT")]


def _connect() -> sqlite3.Connection:
    """Open a connection, creating the parent dir + schema on first use.

    sqlite3.connect() does NOT create the parent directory, so a fresh checkout
    would crash without this makedirs.
    """
    parent = os.path.dirname(os.path.abspath(_DB_PATH))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # Add columns missing from DBs created by an earlier schema version, then
    # build any index that depends on a migrated column (must come after ALTER).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(queries)")}
    for col, ddl in _MIGRATIONS:
        if col not in cols:
            conn.execute(ddl)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_queries_product ON queries(product)")
    conn.commit()
    return conn


# ── Intent classification (rule-based) ──────────────────────────────

# Ordered keyword buckets. First bucket with a hit wins; gate signals override.
_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("greeting",    ("你好", "哈囉", "嗨", "謝謝", "感謝", "hi", "hello", "hey", "thanks", "thank you", "bye")),
    # "多少" alone is ambiguous (重量是多少 = spec); require money context.
    ("price",       ("價", "錢", "費用", "多少錢", "預算", "便宜", "貴", "price", "cost", "budget", "cheap", "expensive", "$")),
    ("comparison",  ("比較", "差別", "差異", "哪個", "哪一", "還是", "vs", "versus", "compare", "difference", "better")),
    ("availability", ("有貨", "庫存", "缺貨", "買", "購買", "下單", "出貨", "stock", "available", "buy", "order", "purchase", "shipping")),
    ("spec",        ("規格", "尺寸", "重量", "材質", "容量", "效能", "參數", "支援", "相容", "spec", "size", "weight", "dimension", "capacity", "performance", "support", "compatible")),
]


# Guards trace exposes display labels; map them back to the engine's gate kinds
# so intent classification and the `gate` column use one canonical vocabulary.
_GUARD_LABEL_TO_KIND = {
    "guardrail": "guardrail",
    "priceguard": "price_guard",
    "scopegate": "scope_gate",
}


def classify_intent(query: str, gate: str | None, blocked: bool) -> str:
    """Map a query to a coarse intent bucket using keywords + guard signals.

    Guard signals take priority: a scope-gate block means off-topic regardless
    of wording; a price-guard block means the user asked about price.
    """
    if gate == "scope_gate":
        return "off_topic"
    if gate == "price_guard":
        return "price"
    if gate in ("guardrail", "constraint_filter"):
        return "blocked_other"

    q = (query or "").lower()
    for label, keywords in _INTENT_KEYWORDS:
        if any(k in q for k in keywords):
            return label
    return "other"


# ── Write path ──────────────────────────────────────────────────────

def log_query(
    *,
    query: str,
    response: dict | None,
    profile: str,
    model: str,
    latency_ms: int,
    status: str,
    error: str | None = None,
) -> None:
    """Persist one chat turn. Never raises — logging must not break the query.

    `response` is the dict returned by `_extract_chat_response` (None on the
    error path). `status` is one of ok | blocked | error.
    """
    try:
        resp = response or {}
        retrieval = resp.get("retrieval") or []
        guards = resp.get("guards") or []
        rerank = resp.get("rerank") or {}
        critique = resp.get("critique") or {}

        blocked = bool(resp.get("blocked"))
        blocked_reason = resp.get("blocked_reason") or (error if status == "error" else None)

        # The blocking gate, if any: first guard whose status is "block".
        # Normalize the display label to the engine's canonical gate kind.
        raw_gate = next((g.get("name", "") for g in guards
                         if g.get("status") == "block"), None)
        gate = _GUARD_LABEL_TO_KIND.get((raw_gate or "").lower().replace(" ", ""),
                                        raw_gate.lower() if raw_gate else None)

        scores = [r.get("score") for r in retrieval if isinstance(r.get("score"), (int, float))]
        top_score = max(scores) if scores else None
        avg_score = round(sum(scores) / len(scores), 4) if scores else None
        n_passed = sum(1 for r in retrieval if r.get("passed"))
        # Highest-scoring retrieved source (retrieval is score-ordered already).
        top_source = retrieval[0].get("source") if retrieval else None

        # Primary product = product_id of the highest-scoring chunk that has one.
        # Distinct products touched go into detail for drill-down ("comparison"
        # queries legitimately span several).
        product = next((r.get("product_id") for r in retrieval if r.get("product_id")), None)
        products = list(dict.fromkeys(r.get("product_id") for r in retrieval if r.get("product_id")))

        intent = classify_intent(query, gate, blocked)

        detail = json.dumps(
            {"retrieval": retrieval, "guards": guards, "rerank": rerank or None,
             "critique": critique or None, "products": products, "error": error},
            ensure_ascii=False,
        )

        row = (
            datetime.now(timezone.utc).isoformat(),
            query,
            profile,
            model,
            latency_ms,
            status,
            1 if blocked else 0,
            blocked_reason,
            gate,
            intent,
            product,
            top_score,
            avg_score,
            len(retrieval),
            n_passed,
            top_source,
            rerank.get("kept") if rerank else None,
            rerank.get("total") if rerank else None,
            critique.get("verdict") if critique else None,
            (1 if critique.get("revised") else 0) if critique else None,
            detail,
        )
        conn = _connect()
        try:
            conn.execute(
                """INSERT INTO queries
                   (ts, query, profile, model, latency_ms, status, blocked,
                    blocked_reason, gate, intent, product, top_score, avg_score,
                    n_retrieved, n_passed, top_source, rerank_kept, rerank_total,
                    critic_verdict, critic_revised, detail)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — logging must never break chat
        print(f"[QueryLog] WARNING: failed to log query: {e}")


# ── Read path (analytics) ───────────────────────────────────────────

def _since_clause(days: int) -> tuple[str, list]:
    """Build a WHERE fragment limiting to the last `days` days (0 = all time)."""
    if days and days > 0:
        return " WHERE ts >= datetime('now', ?) ", [f"-{int(days)} days"]
    return "", []


def fetch_stats(days: int = 7) -> dict:
    """Aggregate the last `days` days of queries for the dashboard."""
    where, params = _since_clause(days)
    conn = _connect()
    try:
        cur = conn.cursor()

        total = cur.execute(f"SELECT COUNT(*) c FROM queries{where}", params).fetchone()["c"]

        # Headline counters
        agg = cur.execute(
            f"""SELECT
                  SUM(blocked) blocked,
                  SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) errors,
                  AVG(latency_ms) avg_latency,
                  AVG(top_score) avg_top_score
                FROM queries{where}""",
            params,
        ).fetchone()

        def _grouped(col: str) -> list[dict]:
            rows = cur.execute(
                f"""SELECT {col} k, COUNT(*) c FROM queries{where}
                    {'AND' if where else 'WHERE'} {col} IS NOT NULL
                    GROUP BY {col} ORDER BY c DESC""",
                params,
            ).fetchall()
            return [{"key": r["k"], "count": r["c"]} for r in rows]

        by_intent = _grouped("intent")
        by_gate = _grouped("gate")
        by_status = _grouped("status")
        top_sources = _grouped("top_source")[:10]

        # Top products asked about (primary subject) — the top-retrieved product
        # per query. Maps product_id → marketing-readable name.
        top_products = [
            {"key": display_name(b["key"]) or b["key"], "product_id": b["key"], "count": b["count"]}
            for b in _grouped("product")[:10]
        ]

        # Most-mentioned products — count EVERY product that surfaced in a query's
        # retrieval (detail.products), so comparison opponents and alternatives
        # count too, not just the primary subject. Parsed in Python since the list
        # lives in the detail JSON blob.
        mention_counts: dict[str, int] = {}
        for r in cur.execute(f"SELECT detail FROM queries{where}", params).fetchall():
            try:
                prods = (json.loads(r["detail"]) or {}).get("products") or []
            except (ValueError, TypeError):
                continue
            for pid in prods:
                if pid:
                    mention_counts[pid] = mention_counts.get(pid, 0) + 1
        most_mentioned = [
            {"key": display_name(pid) or pid, "product_id": pid, "count": c}
            for pid, c in sorted(mention_counts.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ]

        # Top questions — group near-identical phrasings (lowercased, trimmed)
        # so marketing sees what people actually ask, by frequency.
        tq = cur.execute(
            f"""SELECT query, COUNT(*) c FROM queries{where}
                {'AND' if where else 'WHERE'} query IS NOT NULL
                GROUP BY lower(trim(query)) ORDER BY c DESC LIMIT 15""",
            params,
        ).fetchall()
        top_questions = [{"query": r["query"], "count": r["c"]} for r in tq]

        # Volume per day
        volume = cur.execute(
            f"""SELECT substr(ts,1,10) day, COUNT(*) c FROM queries{where}
                GROUP BY day ORDER BY day""",
            params,
        ).fetchall()

        # Latency percentiles (approx via ordered offset; fine at this scale)
        lat_rows = cur.execute(
            f"SELECT latency_ms FROM queries{where} {'AND' if where else 'WHERE'} latency_ms IS NOT NULL ORDER BY latency_ms",
            params,
        ).fetchall()
        latencies = [r["latency_ms"] for r in lat_rows]
        p50 = latencies[len(latencies) // 2] if latencies else None
        p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None

        # Knowledge gaps: answered (not blocked) but weak retrieval — recurring
        # questions the KB struggles with. Grouped by normalized query text.
        gaps = cur.execute(
            f"""SELECT query, COUNT(*) c, AVG(top_score) avg_top
                FROM queries{where}
                {'AND' if where else 'WHERE'} blocked = 0 AND top_score IS NOT NULL
                  AND top_score < 0.45
                GROUP BY lower(trim(query))
                ORDER BY c DESC, avg_top ASC LIMIT 10""",
            params,
        ).fetchall()
        knowledge_gaps = [
            {"query": r["query"], "count": r["c"], "avg_top_score": round(r["avg_top"], 4)}
            for r in gaps
        ]

        return {
            "days": days,
            "total": total,
            "blocked": agg["blocked"] or 0,
            "errors": agg["errors"] or 0,
            "blocked_rate": round((agg["blocked"] or 0) / total, 4) if total else 0,
            "avg_latency_ms": round(agg["avg_latency"]) if agg["avg_latency"] else None,
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "avg_top_score": round(agg["avg_top_score"], 4) if agg["avg_top_score"] else None,
            "by_intent": by_intent,
            "by_gate": by_gate,
            "by_status": by_status,
            "top_sources": top_sources,
            "top_products": top_products,
            "most_mentioned": most_mentioned,
            "top_questions": top_questions,
            "volume": [{"day": r["day"], "count": r["c"]} for r in volume],
            "knowledge_gaps": knowledge_gaps,
        }
    finally:
        conn.close()


def fetch_recent(limit: int = 50, offset: int = 0) -> list[dict]:
    """Most-recent queries first, for the dashboard's history table."""
    conn = _connect()
    try:
        rows = conn.execute(
            """SELECT id, ts, query, profile, model, latency_ms, status, blocked,
                      blocked_reason, gate, intent, product, top_score, n_retrieved, n_passed
               FROM queries ORDER BY id DESC LIMIT ? OFFSET ?""",
            (int(limit), int(offset)),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["product"] = display_name(d.get("product"))  # product_id → readable name
            out.append(d)
        return out
    finally:
        conn.close()


# Columns exported by the CSV download — the human-useful query-history fields,
# in a stable order. The raw `detail` JSON blob is intentionally excluded.
EXPORT_COLUMNS = [
    "id", "ts", "query", "profile", "model", "intent", "product",
    "status", "blocked", "blocked_reason", "gate",
    "top_score", "n_retrieved", "n_passed", "top_source",
    "rerank_kept", "rerank_total", "critic_verdict", "latency_ms",
]


def fetch_all(days: int = 0) -> list[dict]:
    """Every query row in the last `days` days (0 = all time), oldest first.

    Unlike `fetch_recent` there is no LIMIT — this backs the full CSV export, so
    it returns the complete history for the selected range with the product_id
    resolved to its readable name.
    """
    where, params = _since_clause(days)
    cols = ", ".join(EXPORT_COLUMNS)
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT {cols} FROM queries{where} ORDER BY id ASC", params
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["product"] = display_name(d.get("product")) or (d.get("product") or "")
            out.append(d)
        return out
    finally:
        conn.close()
