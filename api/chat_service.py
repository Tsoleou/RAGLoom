"""
Chat orchestration helpers.

Pure functions that translate between the saved pipeline graph and a single
chat turn: strip ingest nodes, inject the user's message + collection input,
and extract reply / retrieval / guards / critique out of the engine results.
No FastAPI / app state here — the chat router owns the pipeline singleton.
"""

import json
from pathlib import Path
from typing import Iterable, Optional

from config.settings import Settings
from core.product_matcher import find_products_in_text

# Product images live beside the product docs and are served at /product_images
# (see api/server.py). Same <product_id>.png convention as the static mount, so a
# matched product_id maps directly to both the on-disk file and the public URL.
_PRODUCT_IMAGES_DIR = Path("knowledge_base/product_images")
_PRODUCT_IMAGES_URL = "/product_images"

# Ingest-side node types skipped when running a chat query — chat ingestion
# goes through /api/chat/ingest, not the graph.
_INGEST_NODE_TYPES = {"loader", "chunker", "embedder", "vectorstore"}
# Nodes that need a `collection` input the chat path must supply (vectorstore
# isn't in the chat subgraph).
_COLLECTION_CONSUMERS = {"retriever", "product_selector"}
# Human-readable name + execution order for the guards trace surfaced to UI.
_GATE_ORDER = [
    ("guardrail",   "Guardrail"),
    ("price_guard", "PriceGuard"),
    ("scope_gate",  "ScopeGate"),
]


def _build_chat_subgraph(graph: dict, user_message: str) -> tuple[list[dict], list[dict]]:
    """Strip ingest nodes and inject the user's message into query_input."""
    nodes_in = graph.get("nodes", []) or []
    edges_in = graph.get("edges", []) or []

    nodes_out: list[dict] = []
    for n in nodes_in:
        if n.get("type") in _INGEST_NODE_TYPES:
            continue
        if n.get("type") == "query_input":
            params = dict(n.get("params") or {})
            params["question"] = user_message
            nodes_out.append({**n, "params": params})
        else:
            nodes_out.append({**n, "params": dict(n.get("params") or {})})

    keep_ids = {n["id"] for n in nodes_out}
    edges_out = [
        e for e in edges_in
        if e.get("source") in keep_ids and e.get("target") in keep_ids
    ]
    return nodes_out, edges_out


def _build_chat_overrides(nodes: list[dict], settings: Settings) -> dict[str, dict]:
    """Inject collection input for nodes whose upstream vectorstore was stripped."""
    collection_info = {
        "client_path": settings.chroma_persist_path,
        "name": "rag_collection",
    }
    overrides: dict[str, dict] = {}
    for n in nodes:
        if n.get("type") in _COLLECTION_CONSUMERS:
            overrides.setdefault(n["id"], {})["collection"] = collection_info
    return overrides


def _build_guards_trace(nodes: list[dict], results: dict[str, dict]) -> list[dict]:
    """Map gate-node statuses (done/blocked/missing) to a UI-friendly trace.

    Stable order = execution order so the panel reads top→bottom intuitively.
    Missing nodes (gate not in this profile's graph) are silently omitted.
    """
    by_type: dict[str, list[dict]] = {}
    for n in nodes:
        by_type.setdefault(n.get("type", ""), []).append(n)

    trace: list[dict] = []
    upstream_blocked = False
    for type_id, label in _GATE_ORDER:
        for n in by_type.get(type_id, []):
            r = results.get(n["id"])
            if r is None:
                # Node exists in graph but engine never reached it
                if upstream_blocked:
                    trace.append({"name": label, "status": "skip", "detail": "upstream blocked"})
                continue
            status = r.get("status")
            if status == "blocked":
                meta = r.get("blocked") or {}
                trace.append({
                    "name": label,
                    "status": "block",
                    "detail": meta.get("matched") or "",
                })
                upstream_blocked = True
            elif status == "done":
                # Pull margin / pass detail from the preview line if present
                preview = (r.get("preview") or "").splitlines()[0]
                detail = preview.replace("✓ Passed", "").strip().lstrip("()").rstrip(")")
                trace.append({
                    "name": label,
                    "status": "pass",
                    "detail": detail or None,
                })
            else:
                trace.append({"name": label, "status": "skip", "detail": status or ""})
    return trace


def _extract_chat_response(
    nodes: list[dict],
    results: dict[str, dict],
    outputs: dict[str, dict],
    settings: Settings,
    catalog_ids: Optional[Iterable[str]] = None,
) -> dict:
    """Pull reply / retrieval / guards / critique out of the engine result set."""
    reply_text = ""
    blocked = False
    blocked_reason = ""

    # Reply preference: result_display preview (already collapses critique/refusal),
    # else generator's GenerationResult.text. If a gate short-circuited, fall back
    # to its refusal_message.
    blocking_meta = None
    for nid, r in results.items():
        if r.get("status") == "blocked" and r.get("blocked"):
            blocking_meta = r["blocked"]
            blocked = True
            blocked_reason = f"{blocking_meta.get('kind', 'gate')}: {blocking_meta.get('matched', '')}".strip(": ")
            reply_text = blocking_meta.get("refusal", "")
            break

    if not reply_text:
        # Answer chain is generator → output_critic → result_display, so the
        # critic's answer_out holds the (possibly revised) final text. Prefer it;
        # fall back to the generator's raw answer when no critic is wired or it
        # never ran. Without this, a successful revise is silently dropped and
        # the user always sees the pre-critic answer.
        for ntype, out_key in (("output_critic", "answer_out"), ("generator", "answer")):
            for n in nodes:
                if n.get("type") == ntype:
                    ans = (outputs.get(n["id"]) or {}).get(out_key)
                    if ans is not None and hasattr(ans, "text"):
                        reply_text = ans.text or ""
                        break
            if reply_text:
                break

    # Retrieval rows from the first retriever node that ran
    retrieval_rows: list[dict] = []
    threshold = settings.score_threshold
    top_k = settings.top_k
    for n in nodes:
        if n.get("type") != "retriever":
            continue
        params = n.get("params") or {}
        threshold = float(params.get("score_threshold", threshold) or threshold)
        top_k = int(params.get("top_k", top_k) or top_k)
        retr_results = (outputs.get(n["id"]) or {}).get("results") or []
        for r in retr_results:
            retrieval_rows.append({
                "source": r.chunk.metadata.get("filename", "unknown"),
                "product_id": r.chunk.metadata.get("product_id") or None,
                "score": round(r.score, 4),
                "distance": round(r.distance, 4),
                "passed": r.score >= threshold,
                "preview": r.chunk.text[:200],
            })
        break

    # Rerank trace from the first retrieval_judge node that ran
    rerank: dict | None = None
    for n in nodes:
        if n.get("type") != "retrieval_judge":
            continue
        r = results.get(n["id"])
        if not r or r.get("status") != "done":
            continue
        try:
            obj = json.loads(r.get("preview") or "")
            if isinstance(obj, dict) and obj.get("__rerank"):
                rerank = {
                    "kept": int(obj.get("kept", 0)),
                    "total": int(obj.get("total", 0)),
                    "verdicts": obj.get("verdicts") or [],
                }
        except (ValueError, json.JSONDecodeError):
            pass
        break

    # Product images: one per product the *reply* actually names. Matched against
    # the full catalog so a specific model isn't mis-attributed to its bare stem,
    # then bounded to the product_ids that were retrieved so a hallucinated name
    # (gemma3:4b is prone to inventing products) can't resolve to a real image,
    # and gated on the PNG existing on disk so a not-yet-photographed product just
    # shows no image. Skipped on blocked answers — a refusal has no product.
    product_images = (
        _resolve_product_images(reply_text, retrieval_rows, catalog_ids) if not blocked else []
    )

    guards = _build_guards_trace(nodes, results)

    # Critique: the critic stores a JSON-encoded preview line with {__critic, verdict, reason, revised, grounded}
    critique = None
    for n in nodes:
        if n.get("type") != "output_critic":
            continue
        r = results.get(n["id"])
        if not r:
            continue
        preview = r.get("preview") or ""
        try:
            obj = json.loads(preview)
            if isinstance(obj, dict) and obj.get("__critic"):
                critique = {
                    "verdict": obj.get("verdict") or "",
                    "reason": obj.get("reason") or "",
                    "revised": bool(obj.get("revised")),
                    "grounded": bool(obj.get("grounded")),
                }
        except (ValueError, json.JSONDecodeError):
            pass
        break

    return {
        "status": "ok",
        "reply": reply_text,
        "retrieval": retrieval_rows,
        "threshold": threshold,
        "top_k": top_k,
        "blocked": blocked,
        "blocked_reason": blocked_reason or None,
        "guards": guards,
        "rerank": rerank,
        "critique": critique,
        "product_images": product_images,
    }


def _resolve_product_images(
    reply_text: str,
    retrieval_rows: list[dict],
    catalog_ids: Optional[Iterable[str]] = None,
) -> list[dict]:
    """Map a reply to the images of the products it names (see call site).

    Names are matched against the *full catalog* (catalog_ids ∪ retrieved) when a
    catalog is supplied, so a specific model ('VisionBook Studio') isn't
    mis-attributed to its bare-stem product ('visionbook'). The result is then
    bounded to the retrieved ids (hallucination guard) and to PNGs on disk.
    Retrieval order is the display order — ranked and deterministic even though
    the match pool is a set. Without a catalog it degrades to retrieved-only.
    """
    retrieved_ids = [
        pid for pid in dict.fromkeys(r.get("product_id") for r in retrieval_rows) if pid
    ]
    if not retrieved_ids:
        return []
    match_pool = set(retrieved_ids)
    if catalog_ids:
        match_pool |= set(catalog_ids)
    named = set(find_products_in_text(reply_text, match_pool))
    images: list[dict] = []
    for pid in retrieved_ids:
        if pid in named and (_PRODUCT_IMAGES_DIR / f"{pid}.png").is_file():
            images.append({"product_id": pid, "url": f"{_PRODUCT_IMAGES_URL}/{pid}.png"})
    return images
