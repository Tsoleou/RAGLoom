# RAGLoom

**Visual RAG pipeline editor for local, on-device product Q&A.**

A locally-hosted RAG (Retrieval-Augmented Generation) system for product Q&A. Drop in product spec documents, ask questions in natural language, and get grounded answers — generated entirely on-device with no cloud dependencies.

Built with Python, FastAPI, React, ChromaDB, and Ollama. No LangChain.

## Features

- **Visual node editor** — drag-and-drop pipeline building with real-time per-node execution status pushed over WebSocket. Fourteen node types covering the full ingest and query path.
- **Safety Guardrail** — keyword-based pre-retrieval filter that blocks queries matching a configurable block list (e.g. competitor brand names) before they ever reach the LLM. In the node view, blocked queries short-circuit downstream nodes with an amber status ring.
- **OutputCritic** — an optional second LLM pass that audits the generator's answer against a negative-rules list. `audit` mode labels violations; `revise` mode rewrites the offending answer.
- **Persona presets** — the `SystemPrompt` node ships with `professional` and `chatbot` presets (plus free-form custom text), both tuned to a trade-show promoter register (2–4 sentences, lead with the hook). The `chatbot` preset emits structured JSON `{reply, emotion}`, which the UI smart-renders as an emotion badge plus a reply bubble with an animated avatar. Replies are always in the visitor's language.
- **Always-on reference data** — the `ReferenceLoader` node loads a static reference file (e.g. a product comparison CSV) and injects it directly into every prompt, guaranteeing broad coverage for comparison queries independent of vector retrieval results.
- **Metadata-filtered retrieval** — product documents are tagged with a `product_id` at ingest time (derived from filename). The `Retriever` node accepts a filter parameter to scope retrieval to a single product. The optional `ProductSelector` node uses one small LLM call to classify query intent and feed the filter automatically.
- **Inline editing** — the `QueryInput` node lets you type the question directly on the node; no config panel round-trip.

## Architecture

```
Ingest:  Document → Loader → Chunker → Embedder → VectorStore (ChromaDB)

Query:   Question → Guardrail → Retriever ──────────────► PromptBuilder → Generator → OutputCritic → Answer
                                    ↑                           ↑               ↑
                             [ProductSelector]           ReferenceLoader    SystemPrompt
                              (optional filter)           (always-on ref)  (persona + format)
```

### Core Modules

| Module | Description |
|--------|-------------|
| `core/loader.py` | Reads `.txt`, `.md`, `.csv`, `.pdf` files; derives `product_id` metadata from filenames matching `product_*.{ext}` |
| `core/chunker.py` | Fixed-length, section-based, and CSV row chunking |
| `core/embedder.py` | Generates embeddings via Ollama API |
| `core/vector_store.py` | ChromaDB persistent storage and retrieval |
| `core/retriever.py` | Semantic search with keyword boosting |
| `core/guardrail.py` | Keyword-based query filter with word-boundary matching |
| `core/prompt_builder.py` | Context assembly (RAG results + glossary + vision) |
| `core/personas.py` | Persona presets (professional / chatbot / custom) |
| `core/generator.py` | Calls Ollama LLM for answer generation |
| `core/critic.py` | Second-pass self-critique (audit / revise modes) |
| `core/product_selector.py` | LLM-based intent classifier that maps a query to a single `product_id` (or `NONE` for ambiguous/comparison queries) |
| `core/pipeline.py` | Orchestrates `ingest()` and `query()` for the chat interface |
| `config/settings.py` | Dataclass-based config with `.env` override support |

## Interfaces

**Chat UI** — for end users. Type a question, get a formatted answer with a retrieval details panel showing which document chunks were used. Blocked queries surface with an amber `⊘ Blocked by Guardrail` label. In chatbot mode, the avatar reflects the LLM's self-reported emotion from the structured JSON output. Replies are always in the visitor's language.

**Node Editor** — for builders and operators. Drag-and-drop pipeline editor with fourteen node types including `Guardrail`, `SystemPrompt`, `OutputCritic`, `ReferenceLoader`, and `ProductSelector`. Real-time per-node execution status over WebSocket. The `ResultDisplay` node smart-renders chatbot JSON into an emotion badge plus reply text. A **Load Default** button restores the default 13-node pipeline at any time.

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
