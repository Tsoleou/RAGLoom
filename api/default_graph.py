"""
Default pipeline graph — server-side single source of truth.

The Editor canvas fetches this on mount; the chat path uses the same builder
when a profile carries no saved graph. Keeping one builder here (rather than
duplicating in the frontend) means Editor and Chat can never drift on retriever
params, anchor lists, edge wiring, etc.
"""

import json


def _default_chat_graph() -> dict:
    """Server-side single source of truth for the default pipeline graph.

    Includes both the ingest chain (loader → chunker → embedder → vectorstore)
    and the full query chain (guardrails → retriever → rerank → scope_gate →
    prompt_builder → generator → critic → display, plus SystemPrompt /
    ReferenceLoader / ProductSelector side-branches). The Editor canvas
    fetches this whole thing on mount; ChatView's `/api/chat/query` strips
    out the ingest nodes at runtime (chat uses /api/chat/ingest separately).

    Keeping one builder here — rather than duplicating in frontend — means
    Editor and Chat can never drift on retriever params, anchor lists, edge
    wiring, etc.
    """
    GAP_X = 280
    Y_INGEST = 80    # ingest row
    Y_QUERY = 340    # main query row
    Y_AUX = 540      # auxiliary row (sysprompt / refloader / pselector)
    QO = 420         # query-row x-offset (query_input is wider than other nodes)

    # ── Ingest row ──────────────────────────────────────────────────
    nodes: list[dict] = [
        {"id": "loader",     "type": "loader",     "position": {"x": 0,           "y": Y_INGEST},
         "params": {"source_path": "./knowledge_base"}},
        {"id": "chunker",    "type": "chunker",    "position": {"x": GAP_X,       "y": Y_INGEST},
         "params": {"strategy": "section", "chunk_size": 500, "chunk_overlap": 50}},
        {"id": "embedder",   "type": "embedder",   "position": {"x": GAP_X * 2,   "y": Y_INGEST},
         "params": {"model": "nomic-embed-text"}},
        {"id": "vstore",     "type": "vectorstore","position": {"x": GAP_X * 3,   "y": Y_INGEST},
         "params": {"persist_path": "./chroma_db", "collection_name": "rag_collection",
                    "wipe_collection": False}},
    ]

    # ── Query row ───────────────────────────────────────────────────
    nodes.extend([
        {"id": "qinput",     "type": "query_input",      "position": {"x": 0,             "y": Y_QUERY},
         "params": {"question": ""}},
        {"id": "guardrail",  "type": "guardrail",        "position": {"x": QO,            "y": Y_QUERY},
         "params": {"blocked_keywords": "asus, acer, msi, hp, dell, apple", "refusal_message": ""}},
        {"id": "priceguard", "type": "price_guard",      "position": {"x": QO + GAP_X,     "y": Y_QUERY},
         "params": {}},
        {"id": "retriever",  "type": "retriever",        "position": {"x": QO + GAP_X * 2, "y": Y_QUERY},
         "params": {"top_k": 5, "score_threshold": 0.0, "keyword_boost": 0.3,
                    "embedding_model": "nomic-embed-text", "product_filter": ""}},
        {"id": "rerank",     "type": "retrieval_judge",  "position": {"x": QO + GAP_X * 3, "y": Y_QUERY},
         "params": {"model": "gemma3:4b"}},
        {"id": "scopegate",  "type": "scope_gate",       "position": {"x": QO + GAP_X * 4, "y": Y_QUERY},
         "params": {"mode": "semantic", "margin_threshold": -0.25, "min_score": 0.7,
                    "embedding_model": "nomic-embed-text"}},
        {"id": "cfilter",    "type": "constraint_filter","position": {"x": QO + GAP_X * 5, "y": Y_QUERY},
         "params": {}},
        {"id": "pbuilder",   "type": "prompt_builder",   "position": {"x": QO + GAP_X * 6, "y": Y_QUERY},
         "params": {"glossary": ""}},
        {"id": "generator",  "type": "generator",        "position": {"x": QO + GAP_X * 7, "y": Y_QUERY},
         "params": {"model": "gemma3:4b", "format_type": ""}},
        {"id": "critic",     "type": "output_critic",    "position": {"x": QO + GAP_X * 8, "y": Y_QUERY},
         "params": {
             "criteria": (
                 "Do not mention competitor brand names like Asus, Acer, MSI, HP, Dell, or Apple.\n"
                 "Do not promise specific pricing, availability, or release dates.\n"
                 'Do not use marketing buzzwords like "amazing", "revolutionary", "industry-leading", "best-in-class".'
             ),
             "mode": "revise",
             "model": "gemma3:4b",
         }},
        {"id": "display",    "type": "result_display",   "position": {"x": QO + GAP_X * 9, "y": Y_QUERY},
         "params": {}},
    ])

    # ── Auxiliary row ───────────────────────────────────────────────
    nodes.extend([
        {"id": "pselector",  "type": "product_selector", "position": {"x": QO + GAP_X * 2, "y": Y_AUX},
         "params": {"mode": "rule", "model": "gemma3:4b", "aliases": json.dumps({
             "starforge": ["星鋒", "星峰"],
             "visionbook": ["維森書", "視覺書"],
             "novapad": ["諾瓦", "諾瓦帕"],
             "titanbook": ["泰坦書", "鈦書"],
             "luminos": ["璐米諾", "流明"],
         }, ensure_ascii=False, indent=2)}},
        {"id": "refloader",  "type": "reference_loader", "position": {"x": QO + GAP_X * 5, "y": Y_AUX},
         "params": {"source_path": "./knowledge_base/_reference"}},
        {"id": "sysprompt",  "type": "system_prompt",    "position": {"x": QO + GAP_X * 7, "y": Y_AUX},
         "params": {"preset": "professional", "text": ""}},
    ])

    edges = [
        # Ingest chain
        {"source": "loader",     "target": "chunker",    "sourceHandle": "documents",      "targetHandle": "documents"},
        {"source": "chunker",    "target": "embedder",   "sourceHandle": "chunks",         "targetHandle": "chunks"},
        {"source": "chunker",    "target": "vstore",     "sourceHandle": "chunks",         "targetHandle": "chunks"},
        {"source": "embedder",   "target": "vstore",     "sourceHandle": "embeddings",     "targetHandle": "embeddings"},
        # Query chain
        {"source": "qinput",     "target": "guardrail",  "sourceHandle": "query",          "targetHandle": "query_in"},
        {"source": "guardrail",  "target": "priceguard", "sourceHandle": "query_out",      "targetHandle": "query_in"},
        {"source": "priceguard", "target": "retriever",  "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "vstore",     "target": "retriever",  "sourceHandle": "collection",     "targetHandle": "collection"},
        # Product selector — wired in so flipping mode='llm' works zero-config; output
        # feeds retriever's product_id filter
        {"source": "priceguard", "target": "pselector",  "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "vstore",     "target": "pselector",  "sourceHandle": "collection",     "targetHandle": "collection"},
        {"source": "refloader",  "target": "pselector",  "sourceHandle": "reference_data", "targetHandle": "reference_data"},
        {"source": "pselector",  "target": "retriever",  "sourceHandle": "product_id",     "targetHandle": "product_id"},
        # Retrieval judge — between retriever and scope_gate
        {"source": "priceguard", "target": "rerank",     "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "retriever",  "target": "rerank",     "sourceHandle": "results",        "targetHandle": "results_in"},
        {"source": "priceguard", "target": "scopegate",  "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "rerank",     "target": "scopegate",  "sourceHandle": "results_out",    "targetHandle": "results_in"},
        # Constraint filter — numeric spec gate (e.g. "under 1kg") between scope_gate
        # and prompt_builder. Filters BOTH the retrieved chunks and the reference rows,
        # so a violating product can't slip back via the always-on reference block.
        # Downstream (pbuilder + critic) now reads cfilter's outputs = the final set.
        {"source": "priceguard", "target": "cfilter",    "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "scopegate",  "target": "cfilter",    "sourceHandle": "results_out",    "targetHandle": "results_in"},
        {"source": "refloader",  "target": "cfilter",    "sourceHandle": "reference_data", "targetHandle": "reference_in"},
        {"source": "priceguard", "target": "pbuilder",   "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "cfilter",    "target": "pbuilder",   "sourceHandle": "results_out",    "targetHandle": "results"},
        {"source": "cfilter",    "target": "pbuilder",   "sourceHandle": "reference_out",  "targetHandle": "reference_data"},
        {"source": "pbuilder",   "target": "generator",  "sourceHandle": "prompt",         "targetHandle": "prompt"},
        # SystemPrompt fans persona + format hint into generator + gates
        {"source": "sysprompt",  "target": "generator",  "sourceHandle": "system_prompt",  "targetHandle": "system_prompt"},
        {"source": "sysprompt",  "target": "generator",  "sourceHandle": "format_hint",    "targetHandle": "format_hint"},
        {"source": "sysprompt",  "target": "guardrail",  "sourceHandle": "format_hint",    "targetHandle": "format_hint"},
        {"source": "sysprompt",  "target": "priceguard", "sourceHandle": "format_hint",    "targetHandle": "format_hint"},
        {"source": "sysprompt",  "target": "scopegate",  "sourceHandle": "format_hint",    "targetHandle": "format_hint"},
        {"source": "sysprompt",  "target": "cfilter",    "sourceHandle": "format_hint",    "targetHandle": "format_hint"},
        # Critic also gets persona + format so a grounded regen (when a revise
        # guts the answer) keeps the same voice and output shape.
        {"source": "sysprompt",  "target": "critic",     "sourceHandle": "system_prompt",  "targetHandle": "system_prompt"},
        {"source": "sysprompt",  "target": "critic",     "sourceHandle": "format_hint",    "targetHandle": "format_hint"},
        # Critic grounded mode — see query + final filtered retrieval set + reference data.
        # Reads cfilter outputs (not scopegate/refloader) so it audits exactly what the
        # generator saw after constraint filtering.
        {"source": "generator",  "target": "critic",     "sourceHandle": "answer",         "targetHandle": "answer_in"},
        {"source": "priceguard", "target": "critic",     "sourceHandle": "query_out",      "targetHandle": "query"},
        {"source": "cfilter",    "target": "critic",     "sourceHandle": "results_out",    "targetHandle": "retrieval"},
        {"source": "cfilter",    "target": "critic",     "sourceHandle": "reference_out",  "targetHandle": "reference_data"},
        {"source": "critic",     "target": "display",    "sourceHandle": "answer_out",     "targetHandle": "answer"},
    ]

    return {"nodes": nodes, "edges": edges}


def _ensure_graph(profile: dict) -> dict:
    """Return profile with a valid `graph`. Auto-fills the default for legacy
    profiles that pre-date the chat-runs-graph migration. The profile dict is
    NOT mutated in place — caller decides whether to persist."""
    if isinstance(profile.get("graph"), dict) and profile["graph"].get("nodes"):
        return profile
    patched = dict(profile)
    patched["graph"] = _default_chat_graph()
    patched.pop("preset", None)
    patched.pop("custom_text", None)
    return patched
