"""Chat endpoints: ingest the knowledge base, run a single turn through the
active profile's graph, and reset multi-turn memory.

Owns the `chat_pipe` singleton — the only state these three endpoints share.
"""

import time

from fastapi import APIRouter

from api.chat_service import (
    _build_chat_overrides,
    _build_chat_subgraph,
    _extract_chat_response,
)
from api.default_graph import _default_chat_graph
from api.engine import execute_graph
from api.profiles_store import _load_profiles
from api.query_log import log_query
from api.schemas import ChatQueryRequest
from config.settings import Settings
from core.pipeline import RAGPipeline

router = APIRouter()

# ── Chat pipeline (singleton) ──────────────────────────────────────
chat_pipe: RAGPipeline | None = None


@router.post("/api/chat/ingest")
def chat_ingest():
    """Initialize the chat RAG pipeline and ingest the knowledge base.

    Resets the collection first so renamed or removed source files don't
    leave orphan chunks behind on repeated ingests.
    """
    global chat_pipe
    try:
        chat_pipe = RAGPipeline(Settings(score_threshold=0.0))
        chat_pipe.reset_collection()
        count = chat_pipe.ingest("./knowledge_base")
        return {"status": "ok", "chunks": count}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.post("/api/chat/query")
def chat_query(req: ChatQueryRequest):
    """Run a single chat turn through the active profile's graph."""
    if chat_pipe is None:
        return {"status": "error", "message": "Knowledge base not loaded"}

    if not req.message.strip():
        return {"status": "error", "message": "Empty message"}

    profiles_data = _load_profiles()
    active = profiles_data.get("active") or "default"
    profile = (profiles_data.get("profiles") or {}).get(active) or {}
    graph = profile.get("graph") or _default_chat_graph()

    nodes, edges = _build_chat_subgraph(graph, req.message)
    settings = chat_pipe.config
    overrides = _build_chat_overrides(nodes, settings)

    # Multi-turn memory: the graph engine is stateless, so the chat endpoint
    # owns conversation history. Feed prior turns into the generator node and
    # write the updated history back after the turn. History lives on the
    # chat_pipe singleton and is cleared by /api/chat/reset.
    gen_id = next((n["id"] for n in nodes if n.get("type") == "generator"), None)
    if gen_id is not None:
        overrides.setdefault(gen_id, {})["messages"] = chat_pipe._messages

    # DialogueFlow script: stage + previous intent persist across turns the same
    # way history does. Feed the current stage, the prior turn's intent (so the
    # node can detect a topic switch and reset the script), and history into the
    # node; the new stage is written back after the turn. No-op when the active
    # profile's graph has no DialogueFlow node.
    df_id = next((n["id"] for n in nodes if n.get("type") == "dialogue_flow"), None)
    if df_id is not None:
        df_over = overrides.setdefault(df_id, {})
        df_over["stage_state"] = chat_pipe._stage
        df_over["prev_intent"] = chat_pipe._intent
        df_over["messages"] = chat_pipe._messages

    # IntentRouter classifies from the query edge (dynamic, no override needed).
    ir_id = next((n["id"] for n in nodes if n.get("type") == "intent_router"), None)

    started = time.perf_counter()
    try:
        results, outputs = execute_graph(nodes, edges, input_overrides=overrides, return_outputs=True)
    except Exception as e:
        # Errors are behavior signal too — log the failed turn before returning.
        log_query(
            query=req.message, response=None, profile=active,
            model=settings.llm_model,
            latency_ms=round((time.perf_counter() - started) * 1000),
            status="error", error=str(e),
        )
        return {"status": "error", "message": str(e)}

    # A turn "commits" only when the generator actually produced an answer — i.e.
    # no guard short-circuited it. The generator runs last (after every gate), so
    # its answer is the single authoritative signal that the turn was served.
    gen_answer = (outputs.get(gen_id) or {}).get("answer") if gen_id is not None else None
    turn_committed = gen_answer is not None and hasattr(gen_answer, "messages")

    # Persist ALL conversation state (history, intent, dialogue stage) together,
    # and only on a committed turn. A guard short-circuit — ScopeGate blocking an
    # off-topic question, Guardrail/PriceGuard, etc. — therefore freezes the whole
    # dialogue: refusals never pollute history, and a blocked off-topic turn can't
    # churn the funnel's stage/intent even though IntentRouter / DialogueFlow run
    # upstream of ScopeGate in topo order. This is what makes the guards
    # authoritative over the script state. Intent is written back AFTER the turn:
    # DialogueFlow already consumed the prior value as prev_intent, and this turn's
    # intent becomes next turn's prev.
    if turn_committed:
        chat_pipe._messages = gen_answer.messages
        if ir_id is not None:
            ir_intent = (outputs.get(ir_id) or {}).get("intent")
            if isinstance(ir_intent, str):
                chat_pipe._intent = ir_intent
        if df_id is not None:
            df_stage = (outputs.get(df_id) or {}).get("stage_out")
            if isinstance(df_stage, int):
                chat_pipe._stage = df_stage

    response = _extract_chat_response(nodes, results, outputs, settings)

    # A node-level failure (e.g. Ollama 500 in the generator) is caught *inside*
    # execute_graph — it marks that node status="error" and breaks, but does NOT
    # re-raise, so the except above never fires. Detect the errored node here so
    # the dashboard's error metric reflects real failures instead of logging a
    # silently-empty answer as "ok".
    errored = next((r for r in results.values() if r.get("status") == "error"), None)
    if errored is not None:
        status, error = "error", errored.get("preview")
    else:
        status, error = ("blocked" if response.get("blocked") else "ok"), None

    log_query(
        query=req.message, response=response, profile=active,
        model=settings.llm_model,
        latency_ms=round((time.perf_counter() - started) * 1000),
        status=status, error=error,
    )
    return response


@router.post("/api/chat/reset")
def chat_reset():
    """Clear multi-turn conversation context."""
    if chat_pipe is not None:
        chat_pipe.reset_conversation()
    return {"status": "ok"}
