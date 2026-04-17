"""
FastAPI Server。

提供 REST API 和 WebSocket 端點，供前端節點 UI 使用。

啟動方式：
    source venv/bin/activate
    uvicorn api.server:app --reload --port 8000
"""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── Profile storage ────────────────────────────────────────────────
_PROFILES_PATH = Path("config/chat_profiles.json")
_DEFAULT_PROFILES = {"active": "default", "profiles": {"default": {"preset": "professional", "custom_text": ""}}}

def _load_profiles() -> dict:
    if _PROFILES_PATH.exists():
        return json.loads(_PROFILES_PATH.read_text())
    return dict(_DEFAULT_PROFILES)

def _save_profiles(data: dict) -> None:
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PROFILES_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))

from api.node_registry import get_node_types_json
from api.engine import execute_graph
from config.settings import Settings
from core.guardrail import check_query as guardrail_check
from core.pipeline import RAGPipeline


# ── Chat pipeline (singleton) ──────────────────────────────────────

chat_pipe: RAGPipeline | None = None


# ── Lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Server] RAGLoom API started")
    yield
    print("[Server] RAGLoom API stopped")


# ── App ────────────────────────────────────────────────────────────

app = FastAPI(title="RAGLoom Node API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ─────────────────────────────────────────────────────────

class GraphEdge(BaseModel):
    source: str
    target: str
    sourceHandle: str = ""
    targetHandle: str = ""


class GraphNode(BaseModel):
    id: str
    type: str
    params: dict = {}


class ExecuteRequest(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class ChatQueryRequest(BaseModel):
    message: str
    mode: str = "professional"
    graph_preset: str | None = None
    graph_custom_text: str | None = None

class ChatProfileRequest(BaseModel):
    name: str
    preset: str
    custom_text: str = ""

class ActivateProfileRequest(BaseModel):
    name: str


# ── REST Endpoints ─────────────────────────────────────────────────

@app.get("/api/node-types")
def get_node_types():
    """回傳所有可用的節點類型定義。"""
    return get_node_types_json()


@app.post("/api/execute")
def execute(req: ExecuteRequest):
    """同步執行 graph，回傳所有節點的結果。"""
    nodes = [n.model_dump() for n in req.nodes]
    edges = [e.model_dump() for e in req.edges]

    results = execute_graph(nodes, edges)
    return results


# ── Chat Endpoints ─────────────────────────────────────────────────

@app.post("/api/chat/ingest")
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


@app.post("/api/chat/query")
def chat_query(req: ChatQueryRequest):
    """Run a single chat turn and return the reply plus retrieval details."""
    if chat_pipe is None:
        return {"status": "error", "message": "Knowledge base not loaded"}

    if not req.message.strip():
        return {"status": "error", "message": "Empty message"}

    # Safety guardrail — check before hitting RAG/LLM (saves tokens + latency)
    allowed, refusal, matched = guardrail_check(req.message)
    if not allowed:
        print(f"[Server] Chat query BLOCKED by guardrail (matched: '{matched}')")
        return {
            "status": "ok",
            "reply": refusal,
            "retrieval": [],
            "threshold": chat_pipe.config.score_threshold,
            "top_k": chat_pipe.config.top_k,
            "blocked": True,
            "blocked_reason": f"matched keyword: {matched}",
        }

    try:
        result = chat_pipe.query(
            req.message,
            mode=req.mode,
            graph_preset=req.graph_preset or None,
            graph_custom_text=req.graph_custom_text or None,
        )
        threshold = chat_pipe.config.score_threshold
        retrieval = [
            {
                "source": r.chunk.metadata.get("filename", "unknown"),
                "score": round(r.score, 4),
                "distance": round(r.distance, 4),
                "passed": r.score >= threshold,
                "preview": r.chunk.text[:200],
            }
            for r in (chat_pipe._last_retrieval or [])
        ]
        return {
            "status": "ok",
            "reply": result.text,
            "retrieval": retrieval,
            "threshold": threshold,
            "top_k": chat_pipe.config.top_k,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/profiles")
def get_profiles():
    """Return all profiles and the active profile name."""
    return _load_profiles()

@app.post("/api/profiles")
def save_profile(req: ChatProfileRequest):
    """Create or overwrite a named profile."""
    data = _load_profiles()
    data["profiles"][req.name] = {"preset": req.preset, "custom_text": req.custom_text}
    _save_profiles(data)
    return {"status": "ok", "name": req.name}

@app.post("/api/profiles/activate")
def activate_profile(req: ActivateProfileRequest):
    """Set the active profile."""
    data = _load_profiles()
    if req.name not in data["profiles"]:
        raise HTTPException(status_code=404, detail=f"Profile '{req.name}' not found")
    data["active"] = req.name
    _save_profiles(data)
    return {"status": "ok", "active": req.name}

@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    """Delete a profile (cannot delete 'default')."""
    if name == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default profile")
    data = _load_profiles()
    if name not in data["profiles"]:
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    del data["profiles"][name]
    if data["active"] == name:
        data["active"] = "default"
    _save_profiles(data)
    return {"status": "ok"}


@app.post("/api/chat/reset")
def chat_reset():
    """Clear multi-turn conversation context."""
    if chat_pipe is not None:
        chat_pipe.reset_conversation()
    return {"status": "ok"}


# ── WebSocket Endpoint ─────────────────────────────────────────────

@app.websocket("/api/ws/execute")
async def ws_execute(ws: WebSocket):
    """WebSocket 端點，即時推送每個節點的執行狀態。

    Client 送出 JSON: {"nodes": [...], "edges": [...]}
    Server 逐步推送: {"nodeId": "xxx", "status": "running|done|error", "preview": "..."}
    最後推送: {"type": "complete", "results": {...}}
    """
    await ws.accept()

    try:
        raw = await ws.receive_text()
        data = json.loads(raw)

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        loop = asyncio.get_event_loop()

        # 狀態更新的 callback（從同步 executor 呼叫）
        status_queue: asyncio.Queue = asyncio.Queue()

        def on_status(node_id: str, status: str, preview: str = "") -> None:
            loop.call_soon_threadsafe(
                status_queue.put_nowait,
                {"nodeId": node_id, "status": status, "preview": preview},
            )

        # 在 thread pool 中執行 graph（因為 executor 是同步的）
        async def run_graph():
            return await loop.run_in_executor(
                None,
                lambda: execute_graph(nodes, edges, on_status=on_status),
            )

        # 同時推送狀態和執行 graph
        task = asyncio.create_task(run_graph())

        # 持續讀取 status queue 並推送給 client
        while not task.done():
            try:
                msg = await asyncio.wait_for(status_queue.get(), timeout=0.1)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                continue

        # 推送 queue 中剩餘的狀態
        while not status_queue.empty():
            msg = status_queue.get_nowait()
            await ws.send_json(msg)

        results = task.result()
        await ws.send_json({"type": "complete", "results": results})

    except WebSocketDisconnect:
        print("[Server] WebSocket client disconnected")
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        print(f"[Server] WebSocket error: {e}")
