"""Chat endpoints: ingest the knowledge base, run a single turn through the
active profile's graph, and reset multi-turn memory.

Owns the `chat_pipe` singleton — the only state these three endpoints share.
"""

from fastapi import APIRouter

from api.chat_service import (
    _build_chat_overrides,
    _build_chat_subgraph,
    _extract_chat_response,
)
from api.default_graph import _default_chat_graph
from api.engine import execute_graph
from api.profiles_store import _load_profiles
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

    try:
        results, outputs = execute_graph(nodes, edges, input_overrides=overrides, return_outputs=True)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    # Persist updated history only when the generator actually ran. A guard
    # short-circuit leaves history untouched, so refusals never pollute it —
    # same behavior as the old pipeline.query() path.
    if gen_id is not None:
        gen_answer = (outputs.get(gen_id) or {}).get("answer")
        if gen_answer is not None and hasattr(gen_answer, "messages"):
            chat_pipe._messages = gen_answer.messages

    return _extract_chat_response(nodes, results, outputs, settings)


@router.post("/api/chat/reset")
def chat_reset():
    """Clear multi-turn conversation context."""
    if chat_pipe is not None:
        chat_pipe.reset_conversation()
    return {"status": "ok"}
