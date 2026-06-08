"""Seed the query-analytics DB with realistic mock data for demos.

Inserts a spread of queries across real products, every intent bucket, all
statuses (ok / blocked / error), and a 21-day time window so every dashboard
panel — volume trend, top products, top questions, intents, guards, knowledge
gaps, recent table — has something to show.

Usage:
    python seed_mock_queries.py            # append ~140 mock rows
    python seed_mock_queries.py --reset    # wipe table first, then seed
    python seed_mock_queries.py -n 300     # custom row count

Writes to the same DB the server reads (./data/queries.db, or
$RAG_QUERY_LOG_DB). Deterministic (fixed seed) so demos are repeatable.
"""

import argparse
import json
import random
from datetime import datetime, timedelta, timezone

from api.product_catalog import name_map
from api.query_log import _connect

# Real product_ids from the catalog, with rough "popularity" weights so a few
# products dominate (realistic for a demo: flagships get asked about most).
_HOT = {"starforge_x1": 6, "novapad_lite": 5, "starforge_titan_9000": 4,
        "visionbook_17": 3, "luminos_s14": 3, "titanbook_ws1": 2}

# Intent → question templates ({p} = product display name, {p2} = a second one).
_TEMPLATES = {
    "spec": ["{p} 的規格是什麼", "{p} 重量多少", "{p} 螢幕多大", "{p} 電池續航如何",
             "{p} 散熱好嗎", "{p} 支援 Wi-Fi 7 嗎", "{p} 用什麼 CPU", "{p} 可以擴充記憶體嗎"],
    "price": ["{p} 多少錢", "{p} 價格是多少", "{p} 有優惠嗎", "{p} 現在的售價"],
    "comparison": ["{p} 跟 {p2} 哪個好", "{p} 和 {p2} 差在哪", "{p} 比 {p2} 強嗎"],
    "availability": ["{p} 有貨嗎", "{p} 什麼時候出貨", "{p} 可以下單嗎", "{p} 缺貨了嗎"],
    "greeting": ["你好", "嗨", "哈囉", "謝謝你的協助"],
    "other": ["你們有哪些產品", "保固怎麼算", "可以開發票嗎", "門市在哪"],
}

# Off-topic queries that ScopeGate refuses (status=blocked, gate=scope_gate).
_OFF_TOPIC = ["今天天氣如何", "幫我寫一首詩", "推薦附近的餐廳", "幫我算數學", "明天股市會漲嗎"]

# Recurring weak-retrieval questions → populate the "knowledge gaps" panel.
_GAPS = ["有沒有 Linux 驅動程式", "支援 4G LTE 上網嗎", "可以外接兩台 4K 螢幕嗎",
         "有沒有教育版折扣", "保固可以延長到三年嗎"]


def _weighted_product(rng: random.Random) -> str:
    pool = list(name_map().keys()) or list(_HOT.keys())
    weights = [_HOT.get(pid, 1) for pid in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def _with_siblings(rng: random.Random, primary: str) -> list[str]:
    """Primary product + 0–2 alternatives, as retrieval often surfaces siblings.

    Drives the 'most-mentioned' panel: some products rarely lead a query but keep
    showing up as alternatives, which is exactly the signal we want to expose.
    """
    out = [primary]
    for _ in range(rng.choices([0, 1, 2], weights=[55, 30, 15], k=1)[0]):
        sib = _weighted_product(rng)
        if sib not in out:
            out.append(sib)
    return out


def _ts(rng: random.Random) -> str:
    """A timestamp in the last 21 days, biased toward recent + business hours."""
    day = int(rng.triangular(0, 21, 0))          # mode at 0 → more recent
    hour = int(rng.triangular(8, 22, 14))         # mode mid-afternoon
    minute = rng.randint(0, 59)
    dt = datetime.now(timezone.utc) - timedelta(days=day, hours=24 - hour, minutes=minute)
    return dt.isoformat()


def _row(rng: random.Random, names: dict) -> tuple:
    """Build one INSERT tuple matching the queries table column order."""
    roll = rng.random()

    # Defaults (the common ok-answer case gets overwritten below)
    status, blocked, gate, blocked_reason = "ok", 0, None, None
    product = _weighted_product(rng)
    pname = names.get(product, product)
    mentioned = [product]  # all products that surface in retrieval for this query
    n_retrieved, n_passed = 5, rng.randint(2, 5)
    top_score = round(rng.uniform(0.55, 0.92), 4)
    critic_verdict = rng.choice(["pass", "pass", "pass", "revise"])
    critic_revised = 1 if critic_verdict == "revise" else 0
    latency = rng.randint(4000, 46000)

    if roll < 0.08:
        # Off-topic → ScopeGate block
        query = rng.choice(_OFF_TOPIC)
        intent, status, blocked, gate = "off_topic", "blocked", 1, "scope_gate"
        blocked_reason = "scope_gate: off-topic"
        product, pname = None, None
        n_retrieved, n_passed, top_score = 0, 0, None
        critic_verdict, critic_revised, latency = None, None, rng.randint(300, 1500)
    elif roll < 0.13:
        # Price question that PriceGuard refuses (short-circuits before retrieval)
        query = f"{pname} 可以給我報價嗎"
        intent, status, blocked, gate = "price", "blocked", 1, "price_guard"
        blocked_reason = "price_guard: price_intent"
        n_retrieved, n_passed, top_score = 0, 0, None
        mentioned = []
        critic_verdict, critic_revised, latency = None, None, rng.randint(300, 1500)
    elif roll < 0.16:
        # Backend error (e.g. Ollama 500) — nothing retrieved
        query = rng.choice(_TEMPLATES["spec"]).format(p=pname)
        intent, status = "spec", "error"
        blocked_reason = "Ollama 500: model runner stopped"
        n_retrieved, n_passed, top_score = 0, 0, None
        mentioned = []
        critic_verdict, critic_revised, latency = None, None, rng.randint(200, 3000)
    elif roll < 0.26:
        # Knowledge gap: answered but weak retrieval
        query = rng.choice(_GAPS)
        intent = "spec"
        top_score = round(rng.uniform(0.28, 0.44), 4)
        n_passed = rng.randint(0, 1)
        mentioned = _with_siblings(rng, product)
    else:
        # Normal answered query across intents
        intent = rng.choices(
            ["spec", "comparison", "availability", "greeting", "other"],
            weights=[55, 18, 12, 8, 7], k=1)[0]
        tpl = rng.choice(_TEMPLATES[intent])
        if intent == "greeting":
            query = tpl
            product, pname = None, None
            n_retrieved, n_passed, top_score = 0, 0, None
            mentioned = []
            critic_verdict, critic_revised = None, None
            latency = rng.randint(1500, 6000)
        elif intent == "comparison":
            # Both the subject and the compared-against product are retrieved.
            p2 = _weighted_product(rng)
            query = tpl.format(p=pname, p2=names.get(p2, p2))
            mentioned = list(dict.fromkeys([product, p2]))
        elif intent == "other":
            query = tpl
            product, pname = None, None
            mentioned = []
        else:
            query = tpl.format(p=pname)
            mentioned = _with_siblings(rng, product)

    avg_score = round(top_score * rng.uniform(0.7, 0.95), 4) if top_score else None
    top_source = f"product_{product}.txt" if product else None
    detail = json.dumps({"mock": True, "products": mentioned}, ensure_ascii=False)

    return (
        _ts(rng), query, "default", "gemma3:4b", latency, status, blocked,
        blocked_reason, gate, intent, product, top_score, avg_score,
        n_retrieved, n_passed, top_source, None, None, critic_verdict,
        critic_revised, detail,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=140, help="number of mock rows")
    ap.add_argument("--reset", action="store_true", help="wipe table before seeding")
    args = ap.parse_args()

    rng = random.Random(42)  # deterministic → repeatable demos
    names = name_map()

    conn = _connect()
    try:
        if args.reset:
            conn.execute("DELETE FROM queries")
            print("[seed] cleared existing rows")
        rows = [_row(rng, names) for _ in range(args.n)]
        conn.executemany(
            """INSERT INTO queries
               (ts, query, profile, model, latency_ms, status, blocked,
                blocked_reason, gate, intent, product, top_score, avg_score,
                n_retrieved, n_passed, top_source, rerank_kept, rerank_total,
                critic_verdict, critic_revised, detail)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]
        print(f"[seed] inserted {len(rows)} rows — table now has {total} total")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
