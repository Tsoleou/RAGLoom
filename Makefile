# RAGLoom — 展場 kiosk 部署 + 開發指令。

.PHONY: build up down logs pull-model dev

# ── 展場部署（容器，單一指令）──────────────────────────────────────
build:           ## 建映像（含 build 前端）
	docker compose build

up:              ## 起 ollama + 首次 pull 模型 + api（首跑會自動 ingest）
	docker compose up

down:            ## 停掉所有服務
	docker compose down

logs:            ## 跟著看 api log
	docker compose logs -f api

# 加新模型（客戶擴充）：make pull-model M=llama3.2
pull-model:      ## 在 ollama 服務裡 pull 指定模型（M=<model>）
	docker compose exec ollama ollama pull $(M)

# ── 本機開發（非容器，兩 process 熱重載）──────────────────────────
dev:             ## 印出 dev 啟動指令（保留原開發流程）
	@echo "開發模式（兩個終端機）："
	@echo "  1) source venv/bin/activate && uvicorn api.server:app --reload --port 8000"
	@echo "  2) cd frontend && npm run dev"
	@echo "（dev 不設 RAG_SERVE_MODE：不自動 ingest、走 vite proxy + token）"
