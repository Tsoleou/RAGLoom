# RAGLoom 單一映像：stage1 用 node build 前端，stage2 用 python 服務 API +
# built 前端（單一 origin）。搭配 docker-compose.yml 的 ollama 服務一起跑。

# ── Stage 1: build frontend (index.html 操作者 app + chat.html kiosk) ──
FROM node:22-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build          # → /app/frontend/dist (main + chat 兩頁)

# ── Stage 2: python runtime ──────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

# chromadb 等套件偶爾需要編譯；裝最小 build 工具後清掉 apt 快取。
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# pip install . 需要 pyproject 宣告的 packages 與 readme/license 都在場。
COPY pyproject.toml README.md LICENSE ./
COPY api/ ./api/
COPY core/ ./core/
COPY config/ ./config/
COPY eval/ ./eval/
RUN pip install --no-cache-dir ".[pdf]"   # 含 PDF ingest，方便客戶擴充 KB

# 預設知識庫（客戶可用掛載的 volume 覆蓋 / 擴充），與 built 前端。
COPY knowledge_base/ ./knowledge_base/
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV RAG_SERVE_MODE=1
EXPOSE 8000
CMD ["uvicorn", "api.server:app", "--host", "0.0.0.0", "--port", "8000"]
