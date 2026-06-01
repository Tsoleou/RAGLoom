"""
FastAPI Server。

提供 REST API 和 WebSocket 端點，供前端節點 UI 使用。

啟動方式：
    source venv/bin/activate
    uvicorn api.server:app --reload --port 8000
"""

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.base import BaseHTTPMiddleware

# ── Profile storage ────────────────────────────────────────────────
#
# Layout (per-file):
#   config/profiles/<name>.json   ← user-created profiles, content = {nodes, edges}
#   config/profiles/_active.txt   ← active profile name (1 line)
#
# The 'default' profile lives in code (_default_chat_graph) — no file.
# Legacy single-file config/chat_profiles.json is migrated on first load.
_PROFILES_DIR = Path("config/profiles")
_ACTIVE_PATH = _PROFILES_DIR / "_active.txt"
_LEGACY_PROFILES_PATH = Path("config/chat_profiles.json")
_DEFAULT_NAME = "default"


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
                 "Do not invent technical specifications not present in the source material.\n"
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


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 帶 random suffix 避免並發寫互砍同一個 .tmp
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(8)}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


import re as _re
_PROFILE_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _is_safe_profile_name(name: str) -> bool:
    """嚴格白名單：開頭英數，後續英數/底線/連字號，長度 1–64。
    擋掉 newline、null byte、前後空白、過長檔名、reserved 名字等病態輸入。"""
    return isinstance(name, str) and bool(_PROFILE_NAME_RE.fullmatch(name))


def _profile_path(name: str) -> Path:
    return _PROFILES_DIR / f"{name}.json"


def _list_user_profile_names() -> list[str]:
    if not _PROFILES_DIR.exists():
        return []
    names = []
    for p in sorted(_PROFILES_DIR.glob("*.json")):
        stem = p.stem
        if stem.startswith("_") or stem.startswith("."):
            continue
        names.append(stem)
    return names


def _read_active_name() -> str:
    if _ACTIVE_PATH.exists():
        name = _ACTIVE_PATH.read_text().strip()
        if name:
            return name
    return _DEFAULT_NAME


def _write_active_name(name: str) -> None:
    _atomic_write_text(_ACTIVE_PATH, name + "\n")


def _read_user_profile_graph(name: str) -> dict | None:
    path = _profile_path(name)
    if not path.exists():
        return None
    try:
        graph = json.loads(path.read_text())
    except Exception as e:
        print(f"[Server] Skipping malformed profile {path.name}: {e}")
        return None
    return graph if isinstance(graph, dict) and graph.get("nodes") else None


def _write_user_profile_graph(name: str, graph: dict) -> None:
    _atomic_write_text(_profile_path(name), json.dumps(graph, ensure_ascii=False, indent=2))


def _delete_user_profile_file(name: str) -> bool:
    path = _profile_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def _migrate_legacy_profiles_if_needed() -> None:
    """One-shot: split old config/chat_profiles.json into per-file layout.
    Idempotent — bails out if profiles/ already exists or legacy file is gone."""
    if _PROFILES_DIR.exists():
        return
    if not _LEGACY_PROFILES_PATH.exists():
        return
    try:
        data = json.loads(_LEGACY_PROFILES_PATH.read_text())
    except Exception as e:
        print(f"[Server] Legacy profile migration aborted ({e}); keeping {_LEGACY_PROFILES_PATH}")
        return

    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profiles = data.get("profiles") or {}
    migrated = 0
    for name, profile in profiles.items():
        if name == _DEFAULT_NAME:
            continue  # default lives in code now
        if not _is_safe_profile_name(name):
            print(f"[Server] Skipping unsafe legacy profile name: {name!r}")
            continue
        patched = _ensure_graph(profile)
        graph = patched.get("graph")
        if not isinstance(graph, dict) or not graph.get("nodes"):
            continue
        _write_user_profile_graph(name, graph)
        migrated += 1

    active = data.get("active") or _DEFAULT_NAME
    _write_active_name(active)

    backup = _LEGACY_PROFILES_PATH.with_suffix(_LEGACY_PROFILES_PATH.suffix + ".bak")
    os.replace(_LEGACY_PROFILES_PATH, backup)
    print(f"[Server] Migrated {migrated} profile(s) to {_PROFILES_DIR}/; legacy file backed up at {backup}")


def _load_profiles() -> dict:
    """Assemble {active, profiles:{name:{graph}}} from per-file storage.
    'default' is synthesized from _default_chat_graph().
    Migration is handled at lifespan startup, not here."""
    profiles = {_DEFAULT_NAME: {"graph": _default_chat_graph()}}
    for name in _list_user_profile_names():
        graph = _read_user_profile_graph(name)
        if graph is not None:
            profiles[name] = {"graph": graph}
    active = _read_active_name()
    if active not in profiles:
        active = _DEFAULT_NAME
    return {"active": active, "profiles": profiles}

from api.node_registry import get_node_types_json
from api.engine import execute_graph
from config.settings import Settings
from core.pipeline import RAGPipeline


# ── Chat pipeline (singleton) ──────────────────────────────────────

chat_pipe: RAGPipeline | None = None


# ── Settings + auth token ──────────────────────────────────────────
#
# Token strategy:
#   - 從 .env 讀 RAG_API_TOKEN；若空，server 啟動時 generate 一個並寫進
#     .env.local，前端 vite proxy 從 VITE_API_TOKEN 讀後注入 X-Local-Token。
#   - 任何打到 /api/* 的 HTTP request 都會被 middleware 驗。
#   - WebSocket middleware 不會 fire，所以在 handler 內手動檢查 header。
#
# Import-time load：CORS middleware 在 add_middleware() 時就讀 allow_origins，
# lifespan 太晚；所以在 module import 時就把 Settings 載好。
_LOCAL_ENV_PATH = Path(".env.local")
_TOKEN_HEADER = "X-Local-Token"
_settings: Settings = Settings.from_env()


def _ensure_api_token(settings: Settings) -> str:
    """確保有個 token：env 有給就用，沒給就 generate 並寫 .env.local。"""
    if settings.api_local_token:
        return settings.api_local_token
    token = secrets.token_urlsafe(32)
    try:
        _LOCAL_ENV_PATH.write_text(
            f"# Auto-generated by RAGLoom server. Read by vite.config.ts.\n"
            f"VITE_API_TOKEN={token}\n"
        )
        print(f"[Server] Generated API token, wrote to {_LOCAL_ENV_PATH}")
    except OSError as e:
        print(f"[Server] WARNING: could not write {_LOCAL_ENV_PATH}: {e}")
        print(f"[Server] API token (set this as VITE_API_TOKEN manually): {token}")
    settings.api_local_token = token
    return token


# ── Lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_api_token(_settings)
    # Migration 是 idempotent，lifespan 跑一次足夠；endpoint 內不再呼叫。
    _migrate_legacy_profiles_if_needed()
    print("[Server] RAGLoom API started")
    yield
    print("[Server] RAGLoom API stopped")


# ── App ────────────────────────────────────────────────────────────

app = FastAPI(title="RAGLoom Node API", lifespan=lifespan)


class LocalTokenMiddleware(BaseHTTPMiddleware):
    """驗證 X-Local-Token header。CORS preflight (OPTIONS) 放行。
    若 token 尚未 ready（lifespan 未跑，例如某些 test client）視為 dev-bypass。"""

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        expected = _settings.api_local_token
        if not expected:
            return await call_next(request)

        provided = request.headers.get(_TOKEN_HEADER, "")
        if not secrets.compare_digest(provided, expected):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "invalid or missing X-Local-Token"},
            )
        return await call_next(request)


# Middleware 是 LIFO 套疊，後加的先跑。CORS 要先 handle preflight，所以最後加。
app.add_middleware(LocalTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.api_allowed_origins,
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


# 一個 graph 最多容納的節點數。Batch eval 跑 N cases × M nodes，
# 兩邊都不設上限的話一次請求就能把 Ollama 跑爆。
_MAX_GRAPH_NODES = 100


class ExecuteRequest(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @field_validator("nodes")
    @classmethod
    def _limit_nodes(cls, v):
        if len(v) > _MAX_GRAPH_NODES:
            raise ValueError(f"too many nodes (max {_MAX_GRAPH_NODES})")
        return v


class ChatQueryRequest(BaseModel):
    # 4000 字夠長到塞段落，又能擋自殘式 DoS。
    message: str = Field(..., max_length=4000)


class ChatProfileRequest(BaseModel):
    name: str
    # The full chat graph; required now that chat runs the graph end-to-end.
    graph: dict


class ActivateProfileRequest(BaseModel):
    name: str


class BatchEvalScope(BaseModel):
    mode: str  # "all" | "category" | "ids"
    category: str | None = None
    case_ids: list[str] | None = None

    @field_validator("case_ids")
    @classmethod
    def _limit_case_ids(cls, v):
        if v is not None and len(v) > 50:
            raise ValueError("too many case_ids (max 50)")
        return v


class BatchEvalRequest(BaseModel):
    graph: ExecuteRequest
    scope: BatchEvalScope
    worst_k: int = Field(3, ge=1, le=20)


# ── REST Endpoints ─────────────────────────────────────────────────

@app.get("/api/node-types")
def get_node_types():
    """回傳所有可用的節點類型定義。"""
    return get_node_types_json()


@app.get("/api/default-graph")
def get_default_graph():
    """Return the server's default pipeline graph (ingest + query chains).

    Single source of truth: the Editor canvas fetches this on mount, and the
    chat path uses the same builder when a profile carries no saved graph.
    """
    return _default_chat_graph()


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
        for n in nodes:
            if n.get("type") == "generator":
                ans = (outputs.get(n["id"]) or {}).get("answer")
                if ans is not None and hasattr(ans, "text"):
                    reply_text = ans.text or ""
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
    }


@app.post("/api/chat/query")
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


@app.get("/api/profiles")
def get_profiles():
    """Return all profiles and the active profile name."""
    return _load_profiles()

@app.post("/api/profiles")
def save_profile(req: ChatProfileRequest):
    """Create or overwrite a named user profile with its full chat graph."""
    if req.name == _DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="'default' is reserved — choose another name.")
    if not _is_safe_profile_name(req.name):
        raise HTTPException(
            status_code=400,
            detail="Profile name must start with a letter/digit and contain only [A-Za-z0-9_-], length 1–64.",
        )
    if not isinstance(req.graph, dict) or not req.graph.get("nodes"):
        raise HTTPException(status_code=400, detail="Profile graph must include nodes.")
    _write_user_profile_graph(req.name, req.graph)
    return {"status": "ok", "name": req.name}

@app.post("/api/profiles/activate")
def activate_profile(req: ActivateProfileRequest):
    """Set the active profile."""
    available = _load_profiles()["profiles"]
    if req.name not in available:
        raise HTTPException(status_code=404, detail=f"Profile '{req.name}' not found")
    _write_active_name(req.name)
    return {"status": "ok", "active": req.name}

@app.delete("/api/profiles/{name}")
def delete_profile(name: str):
    """Delete a user profile (cannot delete 'default')."""
    if name == _DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="Cannot delete the default profile")
    if not _is_safe_profile_name(name):
        raise HTTPException(status_code=400, detail="Invalid profile name")
    if not _delete_user_profile_file(name):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    if _read_active_name() == name:
        _write_active_name(_DEFAULT_NAME)
    return {"status": "ok"}


@app.post("/api/chat/reset")
def chat_reset():
    """Clear multi-turn conversation context."""
    if chat_pipe is not None:
        chat_pipe.reset_conversation()
    return {"status": "ok"}


# ── Batch eval ─────────────────────────────────────────────────────

_GOLDEN_SET_PATH_DEFAULT = Path("eval/golden_set.json")


def _load_golden_set_cases() -> list[dict]:
    if not _GOLDEN_SET_PATH_DEFAULT.exists():
        return []
    try:
        data = json.loads(_GOLDEN_SET_PATH_DEFAULT.read_text())
    except Exception as e:
        print(f"[BatchEval] Failed to load golden set: {e}")
        return []
    return data.get("cases") or []


def _select_cases(scope: BatchEvalScope) -> list[dict]:
    cases = _load_golden_set_cases()
    mode = (scope.mode or "all").lower()
    if mode == "all":
        return cases
    if mode == "category":
        cat = scope.category or ""
        return [c for c in cases if (c.get("category") or "") == cat]
    if mode == "ids":
        wanted = set(scope.case_ids or [])
        return [c for c in cases if c.get("id") in wanted]
    raise HTTPException(status_code=400, detail=f"Unknown scope mode: {scope.mode}")


_METRIC_NODE_TYPES = {
    "coverage_metric": "coverage",
    "score_distribution_metric": "score_distribution",
    "diversity_metric": "diversity",
    "facts_coverage_metric": "facts_coverage",
}


def _harvest_metrics(nodes: list[dict], outputs: dict) -> dict:
    """For each metric node type present in the graph, pull its first occurrence's
    `metric` output. Metric nodes the user didn't include are simply absent."""
    harvested: dict = {key: None for key in _METRIC_NODE_TYPES.values()}
    for n in nodes:
        ntype = n.get("type")
        key = _METRIC_NODE_TYPES.get(ntype)
        if key is None or harvested.get(key) is not None:
            continue
        node_out = outputs.get(n["id"]) or {}
        metric = node_out.get("metric")
        if isinstance(metric, dict):
            harvested[key] = metric
    return harvested


_BATCH_EVAL_TIMEOUT_S = 600  # 10 分鐘上限，避免單請求 block worker 無限久


@app.post("/api/eval/batch")
async def batch_eval(req: BatchEvalRequest):
    """Run the editor graph once per selected golden_set case, harvest metrics
    from coverage/score_distribution/diversity/facts_coverage nodes, return
    per-case results plus aggregate (macro, per-category, worst-K).

    Requires the graph to contain an eval_case_loader — its case_id param is
    overridden per iteration. Other node params are preserved as-is.

    Bounds: graph ≤ 100 nodes, cases ≤ 50, worst_k ≤ 20, timeout 600s.
    """
    from copy import deepcopy
    from core.eval_metrics import aggregate_batch

    nodes = [n.model_dump() for n in req.graph.nodes]
    edges = [e.model_dump() for e in req.graph.edges]
    if not nodes:
        raise HTTPException(status_code=400, detail="Graph has no nodes")

    loader_node = next((n for n in nodes if n.get("type") == "eval_case_loader"), None)
    if loader_node is None:
        raise HTTPException(
            status_code=400,
            detail="Graph must contain an eval_case_loader node",
        )

    selected = _select_cases(req.scope)
    if not selected:
        return {
            "per_case": [],
            "aggregate": aggregate_batch([], worst_k=req.worst_k),
            "skipped": [],
        }
    if len(selected) > 50:
        raise HTTPException(status_code=400, detail="too many cases selected (max 50)")

    def _run() -> dict:
        per_case = []
        skipped = []
        loader_id = loader_node["id"]
        for case in selected:
            case_id = case.get("id")
            case_nodes = deepcopy(nodes)
            for n in case_nodes:
                if n.get("id") == loader_id:
                    params = dict(n.get("params") or {})
                    params["case_id"] = case_id
                    n["params"] = params
                    break

            try:
                _node_results, outputs = execute_graph(
                    case_nodes, edges, return_outputs=True
                )
            except Exception as e:
                skipped.append({"case_id": case_id, "reason": f"graph error: {e}"})
                continue

            metrics = _harvest_metrics(case_nodes, outputs)
            per_case.append({
                "case_id": case_id,
                "category": case.get("category") or "uncategorized",
                "metrics": metrics,
            })

        return {
            "per_case": per_case,
            "aggregate": aggregate_batch(per_case, worst_k=req.worst_k),
            "skipped": skipped,
        }

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_run),
            timeout=_BATCH_EVAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"batch eval exceeded {_BATCH_EVAL_TIMEOUT_S}s timeout",
        )


@app.get("/api/eval/cases")
def get_golden_set_cases():
    """List all golden_set cases (id + category) for the batch-scope UI."""
    cases = _load_golden_set_cases()
    return [
        {"id": c.get("id"), "category": c.get("category") or "uncategorized"}
        for c in cases
    ]


# ── WebSocket Endpoint ─────────────────────────────────────────────

@app.websocket("/api/ws/execute")
async def ws_execute(ws: WebSocket):
    """WebSocket 端點，即時推送每個節點的執行狀態。

    Client 送出 JSON: {"nodes": [...], "edges": [...]}
    Server 逐步推送: {"nodeId": "xxx", "status": "running|done|error", "preview": "..."}
    最後推送: {"type": "complete", "results": {...}}
    """
    expected = _settings.api_local_token
    if expected:
        provided = ws.headers.get(_TOKEN_HEADER, "")
        if not secrets.compare_digest(provided, expected):
            await ws.close(code=4401)
            return

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
