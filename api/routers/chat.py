"""Chat endpoints: ingest the knowledge base, run a single turn through the
active profile's graph, and reset multi-turn memory.

`chat_pipe` is a shared singleton holding the expensive, genuinely-shared RAG
machinery (Chroma collection, spec table, product-id cache). Per-visitor
conversation state (history / dialogue stage / prev intent) does NOT live there
— it lives in the per-session store below, keyed by the client-supplied
session_id, so two visitors talking at once never overwrite each other's stage,
history, or intent. The graph engine is stateless; the chat endpoint feeds the
right session's state in and writes the updated state back per turn.
"""

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field

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
from api.schemas import ChatQueryRequest, ChatResetRequest
from config.settings import Settings
from core.pipeline import RAGPipeline

router = APIRouter()

# ── Chat pipeline (singleton) ──────────────────────────────────────
# Shared, expensive-to-build RAG machinery only. NOT conversation state.
chat_pipe: RAGPipeline | None = None


# ── Per-session conversation state ─────────────────────────────────
@dataclass
class _ChatSession:
    """One visitor's multi-turn state. Mirrors the three fields the chat path
    used to borrow off chat_pipe."""
    messages: list = field(default_factory=list)  # Ollama history (user+assistant)
    stage: int = 0          # DialogueFlow current script stage
    intent: str = ""        # prev turn's routed intent (topic-switch detection)


# Bounded LRU of sessions. Capped so a long-running booth doesn't trade the
# singleton race for an unbounded dict leak (same class as the query-log TTL
# finding). Oldest idle session is evicted when full; an active session is
# move-to-end'd each turn so it survives. The lock guards get-or-create +
# eviction — the only structural mutations of the store; per-turn field writes
# touch a single session object the frontend's chatLoading guard already
# serializes per tab.
_SESSIONS: "OrderedDict[str, _ChatSession]" = OrderedDict()
_SESSIONS_LOCK = threading.Lock()
_MAX_SESSIONS = 500
# id-less requests (old frontend / curl) share this one session — keeps
# single-user behavior intact, but this path alone retains the old shared
# state. Intentional fallback, not an oversight.
_DEFAULT_SESSION_ID = "default"


def _get_session(session_id: str) -> _ChatSession:
    """Fetch (or lazily create) the session for this id, marking it most-recently
    used. Evicts the oldest session when over capacity."""
    sid = session_id.strip() or _DEFAULT_SESSION_ID
    with _SESSIONS_LOCK:
        sess = _SESSIONS.get(sid)
        if sess is None:
            sess = _ChatSession()
            _SESSIONS[sid] = sess
            while len(_SESSIONS) > _MAX_SESSIONS:
                _SESSIONS.popitem(last=False)  # drop oldest idle session
        else:
            _SESSIONS.move_to_end(sid)  # keep active session from being evicted
        return sess


def init_chat_pipe_if_needed() -> int:
    """服務模式啟動時初始化 chat_pipe，讓 chat-only kiosk 不必手動按 Load KB。

    既有 collection 非空 → 直接接上、不重索引（重啟很快、不需 Ollama）；
    collection 空（首次在乾淨 volume 起）→ 跑一次 ingest 建立向量庫。
    只由 lifespan 在 RAG_SERVE_MODE 開啟時呼叫；dev / offline pytest 不觸發，
    因此不會在沒有 Ollama 的環境連線。回傳目前 collection 的 chunk 數。
    """
    global chat_pipe
    if chat_pipe is not None:
        return chat_pipe.collection.count()
    pipe = RAGPipeline(Settings(score_threshold=0.0))
    if pipe.collection.count() == 0:
        pipe.ingest("./knowledge_base")
    chat_pipe = pipe
    return chat_pipe.collection.count()


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

    session = _get_session(req.session_id)

    profiles_data = _load_profiles()
    active = profiles_data.get("active") or "default"
    profile = (profiles_data.get("profiles") or {}).get(active) or {}
    graph = profile.get("graph") or _default_chat_graph()

    nodes, edges = _build_chat_subgraph(graph, req.message)
    settings = chat_pipe.config
    overrides = _build_chat_overrides(nodes, settings)

    # Multi-turn memory: the graph engine is stateless, so the chat endpoint
    # owns conversation history. Feed prior turns into the generator node and
    # write the updated history back after the turn. History lives on this
    # visitor's session and is cleared by /api/chat/reset.
    gen_id = next((n["id"] for n in nodes if n.get("type") == "generator"), None)
    if gen_id is not None:
        overrides.setdefault(gen_id, {})["messages"] = session.messages

    # DialogueFlow script: stage + previous intent persist across turns the same
    # way history does. Feed the current stage, the prior turn's intent (so the
    # node can detect a topic switch and reset the script), and history into the
    # node; the new stage is written back after the turn. No-op when the active
    # profile's graph has no DialogueFlow node.
    df_id = next((n["id"] for n in nodes if n.get("type") == "dialogue_flow"), None)
    if df_id is not None:
        df_over = overrides.setdefault(df_id, {})
        df_over["stage_state"] = session.stage
        df_over["prev_intent"] = session.intent
        df_over["messages"] = session.messages

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
        session.messages = gen_answer.messages
        if ir_id is not None:
            ir_intent = (outputs.get(ir_id) or {}).get("intent")
            if isinstance(ir_intent, str):
                session.intent = ir_intent
        if df_id is not None:
            df_stage = (outputs.get(df_id) or {}).get("stage_out")
            if isinstance(df_stage, int):
                session.stage = df_stage

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
def chat_reset(req: ChatResetRequest):
    """Clear one visitor's multi-turn conversation context.

    Drops only the caller's session so resetting one booth tab can't wipe
    another in-flight visitor's history / dialogue stage.
    """
    sid = req.session_id.strip() or _DEFAULT_SESSION_ID
    with _SESSIONS_LOCK:
        _SESSIONS.pop(sid, None)
    return {"status": "ok"}
