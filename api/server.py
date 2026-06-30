"""
FastAPI Server (app assembly).

Wires settings/auth, lifespan, and the per-domain routers into the `app`
object. Endpoint logic lives in api/routers/*; orchestration helpers in
api/{chat_service,eval_service,profiles_store,default_graph,schemas}.py.

啟動方式：
    source venv/bin/activate
    uvicorn api.server:app --reload --port 8000
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.auth import (
    LocalTokenMiddleware,
    _ensure_api_token,
    _settings,
    admin_challenge,
    check_admin_auth,
)
from api.profiles_store import _migrate_legacy_profiles_if_needed
from api.routers import chat, dashboard, eval, graph, kb, profiles

# 服務模式旗標：由 compose / Makefile serve 設 RAG_SERVE_MODE=1。開啟時 lifespan
# 會自動初始化 chat_pipe（kiosk 免手動 Load KB）。dev --reload 與 offline pytest
# 不設此旗標 → 不自動連 Ollama，行為與既有完全一致。
_SERVE_MODE = bool(os.environ.get("RAG_SERVE_MODE"))
_DIST = Path("frontend/dist")


# ── Lifespan ───────────────────────────────────────────────────────

def _print_ready_banner():
    """啟動就緒時印一段醒目、可點的 URL，讓 `docker compose up` 的操作者一眼
    知道服務起來了、該從哪個網址進。base 預設 localhost:8000，展場換機可用
    RAG_PUBLIC_URL env 覆蓋（不必改 code）。_DIST 存在＝本進程有 serve 前端，
    才指向 :8000 的 UI；純 dev（無 dist、前端走 vite）則指向 :5173。"""
    base = os.environ.get("RAG_PUBLIC_URL", "http://localhost:8000").rstrip("/")
    bar = "=" * 60
    print(bar)
    if _DIST.is_dir():
        print("  ✓ RAGLoom 已就緒，可開啟瀏覽器：")
        print(f"      訪客對話 (kiosk)   →  {base}/")
        print(f"      操作者後台 (admin) →  {base}/admin")
    else:
        print("  ✓ RAGLoom API 已就緒（前端請另開 vite dev）：")
        print(f"      API                →  {base}/")
        print("      前端 (vite dev)    →  http://localhost:5173/")
    print(bar)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_api_token(_settings)
    # Migration 是 idempotent，lifespan 跑一次足夠；endpoint 內不再呼叫。
    _migrate_legacy_profiles_if_needed()
    if _SERVE_MODE:
        try:
            count = chat.init_chat_pipe_if_needed()
            if count < 0:
                print("[Server] Serve mode: KB encrypted & locked — unlock at /admin to start chat")
            else:
                print(f"[Server] Serve mode: chat_pipe ready ({count} chunks)")
        except Exception as e:
            # 別讓初始化失敗擋掉整個 server；admin 仍可手動 Load KB 重試。
            print(f"[Server] WARNING: auto-init chat_pipe failed: {e}")
    print("[Server] RAGLoom API started")
    _print_ready_banner()
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
app.include_router(dashboard.router)
app.include_router(kb.router)

# ── Product images ─────────────────────────────────────────────────
# 產品圖與產品文字資料同住 knowledge_base（source-of-truth 一處），但只掛
# 這個專屬子目錄、不掛 knowledge_base 根 —— 根目錄混有 internal_specs.txt
# 等內部文件，整個掛出去會公開外洩。獨立於 _DIST，backend-only 部署也能 serve。
# /api/* 由 router 先比對，此處只佔 /product_images 前綴，不衝突。
_PRODUCT_IMAGES = Path("knowledge_base/product_images")
if _PRODUCT_IMAGES.is_dir():
    app.mount(
        "/product_images",
        StaticFiles(directory=_PRODUCT_IMAGES),
        name="product_images",
    )

# ── Static frontend ────────────────────────────────────────────────
# 服務模式：FastAPI 同時供應 built 前端與 API（單一 origin、免 CORS）。
#   /        → chat.html  訪客面（純對話 kiosk）
#   /admin   → index.html 操作者面（editor / dashboard / chat with admin）
# Routers 已先註冊，/api/* 優先比對；這裡只掛明確路徑與 /assets，不 mount("/")
# 以免蓋掉 API。dist 不存在（純 dev、未 build）就跳過，保護 --reload 流程。
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    def _serve_kiosk():
        return FileResponse(_DIST / "chat.html")

    @app.get("/admin")
    def _serve_admin(request: Request):
        # 操作者面：設了 admin 密碼就要 Basic Auth 才載入（擋脫離 kiosk 的訪客）。
        if _settings.api_admin_password and not check_admin_auth(request.headers):
            return admin_challenge()
        return FileResponse(_DIST / "index.html")

    @app.get("/favicon.svg")
    def _serve_favicon():
        # public/ 目前沒有 favicon，缺檔回 404 而非讓 FileResponse 拋 500。
        fav = _DIST / "favicon.svg"
        return FileResponse(fav) if fav.is_file() else Response(status_code=404)
else:
    print("[Server] frontend/dist 不存在，略過靜態服務（dev 請用 vite npm run dev）")
