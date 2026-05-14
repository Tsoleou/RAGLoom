# RAGLoom

**Visual RAG pipeline editor for local, on-device product Q&A.**

A locally-hosted RAG (Retrieval-Augmented Generation) system for product Q&A. Drop in product spec documents, ask questions in natural language, and get grounded answers — generated entirely on-device with no cloud dependencies.

Built with Python, FastAPI, React, ChromaDB, and Ollama. No LangChain.

## Features

- **Visual node editor** — drag-and-drop pipeline building with real-time per-node execution status pushed over WebSocket. Fifteen node types covering the full ingest and query path.
- **Safety Guardrail** — keyword-based pre-retrieval filter that blocks queries matching a configurable block list (e.g. competitor brand names) before they ever reach the LLM. In the node view, blocked queries short-circuit downstream nodes with an amber status ring.
- **ScopeGate** — semantic-relevance check that compares the query embedding to on/off-topic anchor phrases living outside the knowledge base. Off-topic queries (pets, recipes, weather, etc.) short-circuit with a canned refusal — no LLM call, no fabricated catalog. Robust to bridge attacks like "is the dog like a laptop?" where retrieval scores alone can't distinguish on- vs off-topic.
- **PriceGuard** — pre-retrieval pattern match for price-intent queries (`price`, `cost`, `MSRP`, `how much`, `售價`, `多少錢`, etc.) that short-circuits to a canned bilingual refusal. Mirrors `Guardrail` and `ScopeGate`: at small-model scale, "trust the model to follow instructions" is unreliable for high-stakes refusals — the policy is enforced in code instead.
- **OutputCritic** — an optional second LLM pass that audits the generator's answer against a negative-rules list. `audit` mode labels violations; `revise` mode rewrites the offending answer.
- **Persona presets** — the `SystemPrompt` node ships with `professional` and `chatbot` presets (plus free-form custom text), both tuned to a trade-show promoter register (2–4 sentences, lead with the hook). The `chatbot` preset emits structured JSON `{reply, emotion}` enforced by Ollama grammar-constrained decoding, which the UI smart-renders as an emotion badge plus a reply bubble with an animated avatar. Replies are always in the visitor's language.
- **Always-on reference data** — the `ReferenceLoader` node loads a static reference file (e.g. a product comparison CSV) and injects it directly into every prompt, guaranteeing broad coverage for comparison queries independent of vector retrieval results.
- **Metadata-filtered retrieval** — product documents are tagged with a `product_id` at ingest time (derived from filename). The `Retriever` node accepts a filter parameter to scope retrieval to a single product. The `ProductSelector` node, included in the default pipeline, classifies query intent in one of two modes — `rule` (fast string matching against the collection's `product_id`s, zero LLM latency) or `llm` (small LLM pass for ambiguous phrasing) — and feeds the filter automatically. Comparison or unrecognized queries fall through to broad search.
- **Inline editing** — the `QueryInput` node lets you type the question directly on the node; no config panel round-trip.
- **Golden-set eval with LLM-as-judge** — `python -m eval.runner --llm-judge` runs a curated regression set through the pipeline and audits each answer with a second LLM call that returns explicit `hallucinated_claims` lists. Gates on the binary signal (claim list empty / non-empty) rather than noisy float scores so same-commit reruns don't flip pass/fail. See [Eval Harness](#eval-harness) below.

![Guardrail node — keyword-based block with amber match indicator](doc/images/KeywordGuardrail.png)

## Architecture

```
Ingest:  Document → Loader → Chunker → Embedder → VectorStore (ChromaDB)

Query:   Question
            │
   Guardrail (API layer, brand keywords) ─ hit ─► canned refusal
            │
            ▼
   PriceGuard (pipeline step 0, price intent) ─ hit ─► canned refusal
            │
            ▼
   ProductSelector ─ product_id ─► Retriever
   (rule | llm)                       │
                                      ▼
                            ScopeGate (semantic off-topic) ─ hit ─► canned refusal
                                      │
                                      ▼
                              PromptBuilder ──► Generator ──► OutputCritic ──► Answer
                                   ▲                 ▲
                            ReferenceLoader     SystemPrompt
                            (always-on ref)   (persona + format)
```

Three pre-LLM guards — `Guardrail`, `PriceGuard`, `ScopeGate` — short-circuit with canned refusals before reaching the generator. Each fires on a different signal (competitor keywords / price intent / semantic off-topic) and they compose without overlap.

### Core Modules

| Module | Description |
|--------|-------------|
| `core/loader.py` | Reads `.txt`, `.md`, `.csv`, `.pdf` files; derives `product_id` metadata from filenames matching `product_*.{ext}` |
| `core/chunker.py` | Fixed-length, section-based, and CSV row chunking |
| `core/embedder.py` | Generates embeddings via Ollama API |
| `core/vector_store.py` | ChromaDB persistent storage and retrieval |
| `core/retriever.py` | Semantic search with keyword boosting |
| `core/guardrail.py` | Keyword-based query filter with word-boundary matching |
| `core/scope_gate.py` | Semantic on/off-topic check via anchor embeddings (default mode) or retrieval-score threshold |
| `core/price_guard.py` | Regex-based price-intent detector + canned bilingual refusal; short-circuits at step 0 of `pipeline.query()` |
| `core/prompt_builder.py` | Context assembly (RAG results + glossary + vision) |
| `core/personas.py` | Persona presets (professional / chatbot / custom) |
| `core/generator.py` | Calls Ollama LLM for answer generation |
| `core/critic.py` | Second-pass self-critique (audit / revise modes) |
| `core/product_selector.py` | LLM-based intent classifier that maps a query to a single `product_id` (or `NONE` for ambiguous/comparison queries) |
| `core/product_matcher.py` | Rule-based intent classifier — word-boundary regex match (CJK-aware via `re.ASCII`) against `product_id`s. Used by the chat pipeline and the `ProductSelector` node's `rule` mode for zero-latency point-query routing. |
| `core/pipeline.py` | Orchestrates `ingest()` and `query()` for the chat interface |
| `config/settings.py` | Dataclass-based config with `.env` override support |

## Interfaces

**Chat UI** — for end users. Type a question, get a formatted answer with a retrieval details panel showing which document chunks were used. Blocked queries surface with an amber `⊘ Blocked by Guardrail` label. In chatbot mode, the avatar reflects the LLM's self-reported emotion from the structured JSON output. Replies are always in the visitor's language.

![Chat UI](doc/images/Chatview.png)

**Node Editor** — for builders and operators. Drag-and-drop pipeline editor with fifteen node types including `Guardrail`, `ScopeGate`, `SystemPrompt`, `OutputCritic`, `ReferenceLoader`, and `ProductSelector`. Real-time per-node execution status over WebSocket. The `ResultDisplay` node smart-renders chatbot JSON into an emotion badge plus reply text. A **Load Default** button restores the default pipeline at any time.

![Node Editor](doc/images/Editorview.png)

When a Guardrail keyword match blocks a query, downstream nodes short-circuit visibly across the graph — a PM can point at the safety layer mid-demo:

![Guardrail blocking downstream nodes across the pipeline](doc/images/guardrails.png)

## Requirements

- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.com/) running locally

### Ollama Models

```bash
ollama pull gemma3:4b
ollama pull nomic-embed-text
```

### Python Dependencies

```bash
pip install fastapi uvicorn pydantic chromadb requests
# Optional: PDF support
pip install pymupdf
```

### Frontend Dependencies

```bash
cd frontend
npm install
```

## Quick Start

```bash
# 1. Clone & setup
git clone https://github.com/Tsoleou/RAGLoom.git
cd RAGLoom

# 2. Start Ollama
ollama serve

# 3. Start the backend
uvicorn api.server:app --reload --port 8000

# 4. Start the frontend (separate terminal)
cd frontend
npm run dev
```

Open `http://localhost:5173` to access the UI. Use the top-right switcher to toggle between **Editor** (node view) and **Chat** (end-user view).

## Knowledge Base

Place product documents in the `knowledge_base/` directory. Supported formats: `.txt`, `.md`, `.csv`, `.pdf`. The pipeline auto-selects a chunking strategy based on file type.

Files matching `product_*.{ext}` are automatically tagged with a `product_id` metadata field (derived from the filename) at ingest time, enabling metadata-filtered retrieval.

Place always-on reference files (e.g. a product comparison CSV) in `knowledge_base/_reference/`. These are loaded at startup and injected directly into every prompt — they are not indexed in the vector store.

## Eval Harness

A small golden-set regression suite lives in `eval/`. Each case in `eval/golden_set.json` declares a question, expected language, optional expected `product_id`, expected facts (keyword recall), and optional `expected_blocked` for guardrail behaviour.

### Rule-based scoring (default)

```bash
python -m eval.runner                              # full set, re-ingest KB
python -m eval.runner --skip-ingest                # reuse existing chroma_db
python -m eval.runner --category single_product_spec
python -m eval.runner --case kb_miss_price_en      # single case (debug)
```

Four deterministic dimensions: `language` (CJK detection vs expected), `retrieval` (expected `product_id` present in retrieved chunks), `faithfulness` (substring recall over `expected_facts` with `match_mode: all | any`), and `relevance` (heuristic — passes if faithfulness ≥ 0.5). Guardrail-blocked cases short-circuit: pass if `expected_blocked == actual_blocked`. Each run writes a JSON report to `eval_results/`.

### LLM-as-judge (optional second pass)

```bash
python -m eval.runner --llm-judge
python -m eval.runner --llm-judge --judge-model qwen2.5:7b
python -m eval.runner --llm-judge --no-hallucination-gate    # calibration mode
```

A second LLM call audits each answer against the retrieved chunks **and** the always-on reference data. The judge returns per-dimension scores plus explicit `supported_claims` / `hallucinated_claims` lists. Output is grammar-constrained via Ollama structured output, so the response is guaranteed to match the schema.

The hallucination gate fires **only on the binary signal** — `passed = rule_pass AND hallucinated_claims == []`. Continuous scores (faithfulness 0.0–1.0, relevance 0.0–1.0) are reported in the JSON output but never veto pass/fail; same-commit reruns of a small model can drift float scores ±0.15 across runs, which would flicker a threshold gate. The `--no-hallucination-gate` flag preserves the judge output for calibration runs without flipping `passed`.

Cases where the pipeline short-circuits (PriceGuard, ScopeGate, Guardrail) skip the judge — there is no retrieved context to audit a canned refusal against.

## Configuration

All settings can be overridden via environment variables (`RAG_` prefix) or a `.env` file:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `RAG_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API address |
| `RAG_LLM_MODEL` | `gemma3:4b` | LLM model name |
| `RAG_EMBEDDING_MODEL` | `nomic-embed-text` | Embedding model name |
| `RAG_CHROMA_PERSIST_PATH` | `./chroma_db` | ChromaDB storage path |
| `RAG_TOP_K` | `5` | Number of chunks to retrieve |
| `RAG_SCORE_THRESHOLD` | `0.3` | Minimum relevance score |
| `RAG_KEYWORD_BOOST` | `0.5` | Keyword boosting weight |
| `RAG_CHUNK_SIZE` | `500` | Chunk size in characters |
| `RAG_CHUNK_OVERLAP` | `50` | Overlap between chunks |
| `RAG_OUTPUT_MODE` | `professional` | Chat UI persona (`professional` / `chatbot`) |

## License

Source Available — free for personal and non-commercial use.
Commercial licensing available: tsoleou@gmail.com
