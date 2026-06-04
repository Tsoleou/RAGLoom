"""
FastAPI Server (app assembly).

Wires settings/auth, lifespan, and the per-domain routers into the `app`
object. Endpoint logic lives in api/routers/*; orchestration helpers in
api/{chat_service,eval_service,profiles_store,default_graph,schemas}.py.

啟動方式：
    source venv/bin/activate
    uvicorn api.server:app --reload --port 8000
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import LocalTokenMiddleware, _ensure_api_token, _settings
from api.profiles_store import _migrate_legacy_profiles_if_needed
from api.routers import chat, eval, graph, profiles


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

# Middleware 是 LIFO 套疊，後加的先跑。CORS 要先 handle preflight，所以最後加。
app.add_middleware(LocalTokenMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.api_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ────────────────────────────────────────────────────────
# Each router declares full /api/... paths (no prefix), so the route table
# matches the pre-split single-file layout exactly.
app.include_router(graph.router)
app.include_router(chat.router)
app.include_router(profiles.router)
app.include_router(eval.router)
