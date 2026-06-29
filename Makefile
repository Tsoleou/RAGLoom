# RAGLoom — 展場 kiosk 部署 + 開發指令。

.PHONY: build up down logs pull-model dev serve-local build-frontend

# ── 展場部署（容器，單一指令）──────────────────────────────────────
build:           ## 建映像（含 build 前端）
	docker compose build

up:              ## 起 ollama + 首次 pull 模型 + api（首跑會自動 ingest；CPU）
	docker compose up
# ⚠️ macOS：Docker 拿不到 Metal GPU，容器 Ollama 純 CPU 跑 4B 會超過 timeout、
#    回覆吐不出來。Mac 開發/驗證請改 `make serve-local`（不經 Docker、接 host Ollama）。

up-gpu:          ## 展場機（NVIDIA GPU）：容器內 ollama 吃 GPU，4B 才跑得動
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up

down:            ## 停掉所有服務
	docker compose down

logs:            ## 跟著看 api log
	docker compose logs -f api

# 加新模型（客戶擴充）：make pull-model M=llama3.2
pull-model:      ## 在 ollama 服務裡 pull 指定模型（M=<model>）
	docker compose exec ollama ollama pull $(M)

# ── 本機開發（非容器，接 host Ollama / Metal）────────────────────
build-frontend:  ## 本機 build 前端到 frontend/dist（serve-local 要 serve UI 前先跑一次）
	cd frontend && npm install && npm run build

serve-local:     ## Mac/快速：本機 serve-mode 單一 origin localhost:8000，接 host Ollama、用現有 chroma_db（不經 Docker、免 vite）
	@test -d frontend/dist || echo "⚠️  frontend/dist 不存在 → 只會 serve API（/ 會 404）。先 make build-frontend 才有 UI。"
	RAG_SERVE_MODE=1 venv/bin/uvicorn api.server:app --host 0.0.0.0 --port 8000

dev:             ## 印出 dev 啟動指令（兩 process 熱重載：uvicorn + vite）
	@echo "開發模式（兩個終端機）："
	@echo "  1) source venv/bin/activate && uvicorn api.server:app --reload --port 8000"
	@echo "  2) cd frontend && npm run dev"
	@echo "（dev 不設 RAG_SERVE_MODE：不自動 ingest、走 vite proxy + token）"
