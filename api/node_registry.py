"""
節點類型 Registry。

定義所有可用的節點類型，包括 I/O port 型別、預設參數、以及前端顯示資訊。
前端的 nodeDefinitions.ts 需與此檔同步。
"""

import json
from dataclasses import dataclass, field

from core.product_matcher import DEFAULT_BRAND_ALIASES


@dataclass
class Port:
    """節點的 I/O 接口定義。"""
    name: str
    data_type: str  # "documents", "chunks", "embeddings", "collection", "results", "prompt", "answer", "query"
    label: str


@dataclass
class ParamDef:
    """節點參數定義。"""
    name: str
    label: str
    param_type: str  # "string", "number", "select"
    default: str | int | float
    options: list[str] = field(default_factory=list)  # for select type


@dataclass
class NodeType:
    """節點類型定義。"""
    type_id: str
    label: str
    label_en: str
    description: str
    category: str  # "ingest", "query", "shared"
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    params: list[ParamDef] = field(default_factory=list)


# ── Node Type Definitions ──────────────────────────────────────────

NODE_TYPES: dict[str, NodeType] = {}


def _register(nt: NodeType) -> None:
    NODE_TYPES[nt.type_id] = nt


# --- Loader ---
_register(NodeType(
    type_id="loader",
    label="Loader",
    label_en="Loader",
    description="Load files or directories into Document objects.",
    category="ingest",
    inputs=[],
    outputs=[Port("documents", "documents", "Documents")],
    params=[
        ParamDef("source_path", "Source Path", "string", "./knowledge_base"),
    ],
))

# --- Chunker ---
_register(NodeType(
    type_id="chunker",
    label="Chunker",
    label_en="Chunker",
    description="Split documents into smaller chunks.",
    category="ingest",
    inputs=[Port("documents", "documents", "Documents")],
    outputs=[Port("chunks", "chunks", "Chunks")],
    params=[
        ParamDef("strategy", "Strategy", "select", "section", options=["section", "csv_row", "fixed"]),
        ParamDef("chunk_size", "Chunk Size", "number", 500),
        ParamDef("chunk_overlap", "Overlap", "number", 50),
    ],
))

# --- Embedder ---
_register(NodeType(
    type_id="embedder",
    label="Embedder",
    label_en="Embedder",
    description="Convert text to vectors via Ollama embedding models.",
    category="shared",
    inputs=[Port("chunks", "chunks", "Chunks")],
    outputs=[Port("embeddings", "embeddings", "Embeddings")],
    params=[
        ParamDef("model", "Model", "string", "nomic-embed-text"),
    ],
))

# --- VectorStore ---
_register(NodeType(
    type_id="vectorstore",
    label="Vector Store",
    label_en="VectorStore",
    description="Store chunks and embeddings in ChromaDB.",
    category="ingest",
    inputs=[
        Port("chunks", "chunks", "Chunks"),
        Port("embeddings", "embeddings", "Embeddings"),
    ],
    outputs=[Port("collection", "collection", "Collection")],
    params=[
        ParamDef("persist_path", "Persist Path", "string", "./chroma_db"),
        ParamDef("collection_name", "Collection Name", "string", "rag_collection"),
    ],
))

# --- Reference Loader ---
_register(NodeType(
    type_id="reference_loader",
    label="Reference Loader",
    label_en="ReferenceLoader",
    description="Load a file or directory as always-on reference material (no chunking, no vector store). Use for small comparison tables / pricing sheets that the LLM should always see.",
    category="query",
    inputs=[],
    outputs=[Port("reference_data", "reference", "Reference Data")],
    params=[
        ParamDef("source_path", "Source Path", "string", "./knowledge_base/_reference"),
    ],
))

# --- Query Input ---
_register(NodeType(
    type_id="query_input",
    label="Query Input",
    label_en="Query Input",
    description="Enter the question to query.",
    category="query",
    inputs=[],
    outputs=[Port("query", "query", "Query Text")],
    params=[
        ParamDef("question", "Question", "string", ""),
    ],
))

# --- Guardrail ---
_register(NodeType(
    type_id="guardrail",
    label="Guardrail",
    label_en="Guardrail",
    description="Block queries containing restricted keywords (e.g., competitor brands). If blocked, short-circuits the pipeline with a refusal message.",
    category="query",
    # Note: input/output port names must differ — ReactFlow handles use port name
    # as the DOM id, so identical names on the same node create id collisions and
    # edges silently fail to render.
    inputs=[Port("query_in", "query", "Query Text")],
    outputs=[Port("query_out", "query", "Query Text")],
    params=[
        ParamDef("blocked_keywords", "Blocked Keywords", "string", "asus, acer, msi, hp, dell, apple"),
        ParamDef(
            "refusal_message",
            "Refusal Message",
            "textarea",
            (
                "I'm sorry, but I can only answer questions about our own products. "
                "For information about other brands, please visit their official channels."
            ),
        ),
    ],
))

# --- Product Selector ---
_register(NodeType(
    type_id="product_selector",
    label="Product Selector",
    label_en="ProductSelector",
    description=(
        "Classify the query to a single product_id, then feed it into Retriever to scope retrieval. "
        "Two modes: 'rule' uses fast string matching against product_ids in the collection (zero LLM latency, "
        "needs the collection input). 'llm' uses a small LLM pass against a product reference table "
        "(needs the reference_data input). Empty output means no clear match — Retriever falls back to broad search."
    ),
    category="query",
    inputs=[
        Port("query", "query", "Query Text"),
        Port("collection", "collection", "Collection"),
        Port("reference_data", "reference", "Reference Data"),
    ],
    outputs=[Port("product_id", "product_id", "Product ID")],
    params=[
        ParamDef("mode", "Mode", "select", "rule", options=["rule", "llm"]),
        ParamDef("model", "Model (LLM mode)", "string", "gemma3:4b"),
        ParamDef(
            "aliases",
            "Brand Aliases (JSON, rule mode)",
            "textarea",
            json.dumps(DEFAULT_BRAND_ALIASES, ensure_ascii=False, indent=2),
        ),
    ],
))

# --- Retriever ---
_register(NodeType(
    type_id="retriever",
    label="Retriever",
    label_en="Retriever",
    description="Retrieve relevant chunks from the vector store.",
    category="query",
    inputs=[
        Port("query", "query", "Query Text"),
        Port("collection", "collection", "Collection"),
        Port("product_id", "product_id", "Product ID"),
    ],
    outputs=[Port("results", "results", "RetrievalResults")],
    params=[
        ParamDef("top_k", "Top K", "number", 3),
        ParamDef("score_threshold", "Score Threshold", "number", 0.0),
        ParamDef("keyword_boost", "Keyword Boost", "number", 0.3),
        ParamDef("embedding_model", "Embedding Model", "string", "nomic-embed-text"),
        ParamDef("product_filter", "Product Filter", "string", ""),
    ],
))

# --- Prompt Builder ---
_register(NodeType(
    type_id="prompt_builder",
    label="Prompt Builder",
    label_en="PromptBuilder",
    description="Assemble retrieval results and query into a context-only prompt. Persona lives on SystemPrompt, format on Generator.",
    category="query",
    inputs=[
        Port("query", "query", "Query Text"),
        Port("results", "results", "RetrievalResults"),
        Port("reference_data", "reference", "Reference Data"),
    ],
    outputs=[Port("prompt", "prompt", "Prompt")],
    params=[
        ParamDef("glossary", "Glossary", "string", ""),
    ],
))

# --- System Prompt ---
_register(NodeType(
    type_id="system_prompt",
    label="System Prompt",
    label_en="SystemPrompt",
    description="Defines persona/tone via a preset (professional / chatbot / custom). Outputs the persona text and a format hint that the Generator can pick up.",
    category="query",
    inputs=[],
    outputs=[
        Port("system_prompt", "system_prompt", "System Prompt"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
    params=[
        ParamDef(
            "preset",
            "Preset",
            "select",
            "professional",
            options=["professional", "chatbot", "custom"],
        ),
        ParamDef(
            "text",
            "Custom Text",
            "textarea",
            (
                "You are a product specialist for a PC manufacturer, helping customers at a live demo station.\n\n"
                "RULES:\n"
                "1. Answer ONLY using facts from [Internal Knowledge]. Never fabricate specs, prices, or model names.\n"
                "2. If the knowledge base doesn't contain the answer, say so honestly and suggest what you can help with instead.\n"
                "3. Keep answers concise (2-4 sentences) — customers are standing at a demo booth, not reading a manual.\n"
                "4. Match the user's language (English, 繁體中文, etc.).\n"
                "5. Tone: Professional, confident, approachable. No marketing fluff."
            ),
        ),
    ],
))

# --- Generator ---
_register(NodeType(
    type_id="generator",
    label="Generator",
    label_en="Generator",
    description="Call Ollama LLM to generate an answer. Optionally takes a SystemPrompt persona and a format hint.",
    category="query",
    inputs=[
        Port("prompt", "prompt", "Prompt"),
        Port("system_prompt", "system_prompt", "System Prompt"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
    outputs=[Port("answer", "answer", "Answer")],
    params=[
        ParamDef("model", "Model", "string", "gemma3:4b"),
        ParamDef("format_type", "Format Override", "select", "", options=["", "json"]),
    ],
))

# --- Output Critic ---
_register(NodeType(
    type_id="output_critic",
    label="Output Critic",
    label_en="OutputCritic",
    description="Run a second LLM pass to check the answer against negative rules. Can audit (label) or revise (rewrite) the answer.",
    category="query",
    inputs=[Port("answer_in", "answer", "Answer")],
    outputs=[Port("answer_out", "answer", "Answer")],
    params=[
        ParamDef(
            "criteria",
            "Negative Rules",
            "textarea",
            (
                "Do not mention competitor brand names like Asus, Acer, MSI, HP, Dell, or Apple.\n"
                "Do not promise specific pricing, availability, or release dates.\n"
                "Do not invent technical specifications not present in the source material.\n"
                "Do not use marketing buzzwords like \"amazing\", \"revolutionary\", \"industry-leading\", \"best-in-class\"."
            ),
        ),
        ParamDef("mode", "Mode", "select", "audit", options=["audit", "revise"]),
        ParamDef("model", "Model", "string", "gemma3:4b"),
    ],
))

# --- Result Display ---
_register(NodeType(
    type_id="result_display",
    label="Result Display",
    label_en="ResultDisplay",
    description="Display the final generated answer.",
    category="query",
    inputs=[Port("answer", "answer", "Answer")],
    outputs=[],
    params=[],
))


def get_node_types_json() -> list[dict]:
    """回傳所有節點類型的 JSON 格式，供前端使用。"""
    result = []
    for nt in NODE_TYPES.values():
        result.append({
            "typeId": nt.type_id,
            "label": nt.label,
            "labelEn": nt.label_en,
            "description": nt.description,
            "category": nt.category,
            "inputs": [{"name": p.name, "dataType": p.data_type, "label": p.label} for p in nt.inputs],
            "outputs": [{"name": p.name, "dataType": p.data_type, "label": p.label} for p in nt.outputs],
            "params": [
                {
                    "name": p.name,
                    "label": p.label,
                    "type": p.param_type,
                    "default": p.default,
                    **({"options": p.options} if p.options else {}),
                }
                for p in nt.params
            ],
        })
    return result
