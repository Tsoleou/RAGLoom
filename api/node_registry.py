"""
節點類型 Registry。

定義所有可用的節點類型，包括 I/O port 型別、預設參數、以及前端顯示資訊。
前端的 nodeDefinitions.ts 需與此檔同步。
"""

import json
from dataclasses import dataclass, field

from core.product_matcher import DEFAULT_BRAND_ALIASES
from core.scope_gate import (
    DEFAULT_ON_TOPIC_ANCHORS,
    DEFAULT_OFF_TOPIC_ANCHORS,
)
from core.dialogue_flow import (
    DEFAULT_STAGES as DIALOGUE_FLOW_DEFAULT_STAGES,
    DEFAULT_SCRIPTS as DIALOGUE_FLOW_DEFAULT_SCRIPTS,
)
from core.intent_router import DEFAULT_INTENTS as INTENT_ROUTER_DEFAULT_INTENTS


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
    param_type: str  # "string", "number", "select", "textarea", "boolean"
    default: str | int | float | bool
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
    description=(
        "Store chunks and embeddings in ChromaDB. Default behavior upserts — "
        "re-running the pipeline updates / inserts but preserves existing data, "
        "so test runs in the Editor don't blow away ChatView's ingested KB. "
        "Toggle `wipe_collection` on for a hard rebuild (drop + reload)."
    ),
    category="ingest",
    inputs=[
        Port("chunks", "chunks", "Chunks"),
        Port("embeddings", "embeddings", "Embeddings"),
    ],
    outputs=[Port("collection", "collection", "Collection")],
    params=[
        ParamDef("persist_path", "Persist Path", "string", "./chroma_db"),
        ParamDef("collection_name", "Collection Name", "string", "rag_collection"),
        ParamDef("wipe_collection", "Wipe Before Write", "boolean", False),
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
    inputs=[
        Port("query_in", "query", "Query Text"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
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

# --- Price Guard ---
_register(NodeType(
    type_id="price_guard",
    label="Price Guard",
    label_en="PriceGuard",
    description=(
        "Block queries asking about price, cost, MSRP, or discounts. The KB "
        "carries no pricing data, but small LLMs fabricate dollar amounts under "
        "direct pressure — detecting the intent in code is more reliable than "
        "prompt rules. Pattern-matches EN + ZH price phrases (\"how much\", \"售價\", "
        "\"$\", \"折扣\", etc.) and short-circuits with a language-aware canned refusal."
    ),
    category="query",
    inputs=[
        Port("query_in", "query", "Query Text"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
    outputs=[Port("query_out", "query", "Query Text")],
    params=[],
))

# --- Scope Gate ---
_register(NodeType(
    type_id="scope_gate",
    label="Scope Gate",
    label_en="ScopeGate",
    description=(
        "Block off-topic queries with a semantic-relevance check. Two modes: "
        "'semantic' (default) compares the query embedding against on/off-topic "
        "anchor phrases that live outside the KB — robust to bridge attacks. "
        "'retrieval' thresholds the top retrieval score (cheaper, but vulnerable "
        "when KB tokens are background noise). Greetings and very short queries "
        "bypass either mode. Short-circuits the pipeline with a language-aware refusal."
    ),
    category="query",
    inputs=[
        Port("results_in", "results", "RetrievalResults"),
        Port("query", "query", "Query Text"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
    outputs=[Port("results_out", "results", "RetrievalResults")],
    params=[
        ParamDef("mode", "Mode", "select", "semantic", options=["semantic", "retrieval"]),
        ParamDef(
            "on_topic_anchors",
            "On-Topic Anchors (semantic mode, one per line)",
            "textarea",
            "\n".join(DEFAULT_ON_TOPIC_ANCHORS),
        ),
        ParamDef(
            "off_topic_anchors",
            "Off-Topic Anchors (semantic mode, one per line)",
            "textarea",
            "\n".join(DEFAULT_OFF_TOPIC_ANCHORS),
        ),
        ParamDef("margin_threshold", "Margin Threshold (semantic mode)", "number", -0.25),
        ParamDef("min_score", "Min Retrieval Score (retrieval mode)", "number", 0.7),
        ParamDef("embedding_model", "Embedding Model (semantic mode)", "string", "nomic-embed-text"),
    ],
))

# --- Retrieval Judge ---
_register(NodeType(
    type_id="retrieval_judge",
    label="Retrieval Judge",
    label_en="RetrievalJudge",
    description=(
        "LLM-as-judge rerank: drops retrieved chunks that don't actually answer "
        "the query. Catches polarity / negation failures that cosine similarity "
        "misses — e.g., a chunk literally saying 'NOT suitable for high-performance' "
        "no longer ranks as a high-performance recommendation. One batched LLM call "
        "per query (independent of K). Degrades to keep-everything on any judge "
        "error so a flaky model can't hide good chunks. A floor guarantees the "
        "judge can never prune below N chunks (restored by retrieval score) so a "
        "tiny over-pruning judge can't starve generation of context."
    ),
    category="query",
    inputs=[
        Port("query", "query", "Query Text"),
        Port("results_in", "results", "RetrievalResults"),
    ],
    outputs=[
        Port("results_out", "results", "RetrievalResults"),
        Port("judge_trace", "judge_trace", "Judge Trace"),
    ],
    params=[
        ParamDef("model", "Model", "string", "gemma3:4b"),
        ParamDef("floor", "Min Kept (floor)", "number", 3),
    ],
))


# --- Judge Trace Inspector ---
_register(NodeType(
    type_id="judge_trace_inspector",
    label="Judge Trace Inspector",
    label_en="JudgeTraceInspector",
    description=(
        "Observation-only sink for the Retrieval Judge's per-chunk verdicts. "
        "Wire Retrieval Judge's 'judge_trace' output here to see, for every "
        "candidate, whether it was kept or dropped and the one-line reason — "
        "independently of the reranked results that flow downstream. Produces "
        "no output; nothing reads from it."
    ),
    category="eval",
    inputs=[
        Port("judge_trace", "judge_trace", "Judge Trace"),
    ],
    outputs=[],
    params=[],
))


# --- Constraint Filter ---
_register(NodeType(
    type_id="constraint_filter",
    label="Constraint Filter",
    label_en="ConstraintFilter",
    description=(
        "Enforce numeric spec constraints from the query in code (no LLM). A regex "
        "extracts a constraint like 'weight < 1kg' (deterministic, no hallucination), "
        "then each candidate's product_id is resolved to its canonical spec from the "
        "reference table and violators are dropped. Both the retrieved chunks AND the "
        "reference rows are filtered, so a violating product can't slip back in via "
        "the always-on reference block. Needed because small models can't reliably "
        "compare numbers (they'll recommend a 1.8kg laptop for 'under 1kg'). No-op "
        "when the query states no numeric constraint. Supports weight (kg), screen "
        "(inch), battery (hr), RAM (GB), and storage (GB/TB); RAM vs storage are "
        "disambiguated by keyword since both use GB."
    ),
    category="query",
    inputs=[
        Port("query", "query", "Query Text"),
        Port("results_in", "results", "RetrievalResults"),
        Port("reference_in", "reference", "Reference Data"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
    outputs=[
        Port("results_out", "results", "RetrievalResults"),
        Port("reference_out", "reference", "Reference Data"),
    ],
    params=[],
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

# --- Intent Router ---
_register(NodeType(
    type_id="intent_router",
    label="Intent Router",
    label_en="IntentRouter",
    description=(
        "Classify the visitor's message into one booth-inquiry intent (spec / "
        "recommend / compare / suitability …) so a downstream DialogueFlow can "
        "pick the matching script. Routing is DYNAMIC — the intent is re-detected "
        "every turn, so a topic-hopping visitor is followed immediately (one small "
        "LLM classification call per turn). Outputs the intent label, or empty "
        "when nothing clearly matches (greeting / small talk) — DialogueFlow then "
        "uses its generic fallback. Edit `intents` (JSON) to add / rename intents; "
        "keep labels in sync with DialogueFlow's scripts."
    ),
    category="query",
    inputs=[
        Port("query", "query", "Query Text"),
    ],
    outputs=[
        Port("intent", "intent", "Intent"),
    ],
    params=[
        ParamDef("model", "Model", "string", "gemma3:4b"),
        ParamDef(
            "intents",
            "Intents (JSON list of {label, description})",
            "textarea",
            json.dumps(INTENT_ROUTER_DEFAULT_INTENTS, ensure_ascii=False, indent=2),
        ),
    ],
))

# --- Dialogue Flow ---
_register(NodeType(
    type_id="dialogue_flow",
    label="Dialogue Flow",
    label_en="DialogueFlow",
    description=(
        "Guided multi-turn inquiry script for a product booth. Sits on the "
        "SystemPrompt → Generator edge — it takes the persona text, appends the "
        "current stage's instruction so the bot knows what to do THIS turn, and "
        "passes the combined text to the Generator. Wire an IntentRouter into "
        "`intent` for DYNAMIC routing: the active script is chosen per turn from "
        "the `scripts` map (spec / recommend / compare / suitability), and the "
        "stage resets when the intent changes. With no intent wired, the flat "
        "`stages` fallback runs (works standalone). Stage CONTROL (which stage, "
        "when to advance) is kept separate from CONTENT (what to say): in the "
        "default 'soft' mode a single constrained YES/NO LLM call decides "
        "advancement, because small models can't reliably self-manage a script "
        "from a free-form prompt. Stage + intent persist across turns and reset "
        "with the conversation."
    ),
    category="query",
    # Ports are named *_in / *_out to avoid same-node DOM id collisions (see
    # Guardrail). `stage_state`, `prev_intent`, `messages` are NOT ports — they
    # arrive via session overrides, exactly like the Generator's `messages`.
    inputs=[
        Port("system_prompt_in", "system_prompt", "System Prompt"),
        Port("query", "query", "Query Text"),
        Port("intent", "intent", "Intent"),
    ],
    outputs=[
        Port("system_prompt_out", "system_prompt", "System Prompt"),
        Port("stage_out", "stage", "Stage"),
    ],
    params=[
        ParamDef("mode", "Mode", "select", "soft", options=["soft"]),
        ParamDef("model", "Gate Model (soft mode)", "string", "gemma3:4b"),
        ParamDef(
            "scripts",
            "Scripts (JSON {intent: [{name, goal, advance_when, instruction}]})",
            "textarea",
            json.dumps(DIALOGUE_FLOW_DEFAULT_SCRIPTS, ensure_ascii=False, indent=2),
        ),
        ParamDef(
            "stages",
            "Fallback Stages (JSON list, used when no intent matches)",
            "textarea",
            json.dumps(DIALOGUE_FLOW_DEFAULT_STAGES, ensure_ascii=False, indent=2),
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
        ParamDef("num_ctx", "Context Window (tokens)", "number", 8192),
        ParamDef("history_limit", "Max History Messages", "number", 12),
    ],
))

# --- Output Critic ---
_register(NodeType(
    type_id="output_critic",
    label="Output Critic",
    label_en="OutputCritic",
    description=(
        "Run a second LLM pass to check the answer against negative rules. "
        "Can audit (label) or revise (rewrite) the answer. When the optional "
        "`query` and `retrieval` ports are wired, the critic switches to "
        "grounded mode and also verifies the answer (1) addresses the question "
        "and (2) stays grounded in the retrieved context — catches hallucinated "
        "specs and off-target answers that rule checks alone miss."
    ),
    category="query",
    inputs=[
        Port("answer_in", "answer", "Answer"),
        Port("query", "query", "Query Text"),
        Port("retrieval", "results", "RetrievalResults"),
        Port("reference_data", "reference", "Reference Data"),
        Port("system_prompt", "system_prompt", "System Prompt"),
        Port("format_hint", "format_hint", "Format Hint"),
    ],
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
        ParamDef("mode", "Mode", "select", "revise", options=["audit", "revise", "revise+regen"]),
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


# ── Eval nodes (Editor-only) ───────────────────────────────────────
# These are observation / debug nodes for retrieval quality. They do NOT
# appear in the default chat graph and are not used by ChatView. Drag them
# in the editor to wire ad-hoc eval pipelines.

# --- Eval Case Loader ---
_register(NodeType(
    type_id="eval_case_loader",
    label="Eval Case Loader",
    label_en="EvalCaseLoader",
    description=(
        "Load one case from eval/golden_set.json by case_id. Emits the "
        "question + expected_product + expected_facts (newline-joined) so "
        "downstream metric nodes get their ground truth wired automatically."
    ),
    category="eval",
    inputs=[],
    outputs=[
        Port("query", "query", "Query Text"),
        Port("expected_product", "string", "Expected Product"),
        Port("expected_facts", "string", "Expected Facts"),
        Port("match_mode", "string", "Match Mode"),
    ],
    params=[
        ParamDef("case_id", "Case ID", "string", "starforge_x1_gpu_en"),
        ParamDef("golden_set_path", "Golden Set Path", "string", "eval/golden_set.json"),
    ],
))

# --- Coverage Metric ---
_register(NodeType(
    type_id="coverage_metric",
    label="Coverage (Hit@K)",
    label_en="CoverageMetric",
    description=(
        "Did expected_product appear in top-K retrieved chunks, and at "
        "what rank? Input port `expected_product` overrides the param "
        "when wired. Mirrors eval/scorer.py Retrieval scoring."
    ),
    category="eval",
    inputs=[
        Port("results", "results", "Retrieval Results"),
        Port("expected_product", "string", "Expected Product"),
    ],
    outputs=[Port("metric", "metric", "Metric")],
    params=[
        ParamDef("expected_product", "Expected Product (fallback)", "string", ""),
        ParamDef("top_k", "Top K", "number", 5),
    ],
))

# --- Score Distribution Metric ---
_register(NodeType(
    type_id="score_distribution_metric",
    label="Score Distribution",
    label_en="ScoreDistributionMetric",
    description=(
        "Top-K similarity score statistics: min/max/mean/std + the gap "
        "between top-1 and top-K. Diagnoses 'all noise' retrieval where "
        "every chunk scores low."
    ),
    category="eval",
    inputs=[Port("results", "results", "Retrieval Results")],
    outputs=[Port("metric", "metric", "Metric")],
    params=[
        ParamDef("top_k", "Top K", "number", 5),
    ],
))

# --- Diversity Metric ---
_register(NodeType(
    type_id="diversity_metric",
    label="Product Diversity",
    label_en="DiversityMetric",
    description=(
        "How many distinct product_ids appear in top-K, plus Shannon "
        "entropy (normalized to log2(top_k)). Useful for comparison "
        "queries — low entropy means retrieval was dominated by one "
        "product even though the question spans many."
    ),
    category="eval",
    inputs=[Port("results", "results", "Retrieval Results")],
    outputs=[Port("metric", "metric", "Metric")],
    params=[
        ParamDef("top_k", "Top K", "number", 5),
    ],
))

# --- Facts Coverage Metric ---
_register(NodeType(
    type_id="facts_coverage_metric",
    label="Facts Coverage",
    label_en="FactsCoverageMetric",
    description=(
        "Retrieval-level analogue of faithfulness — what fraction of "
        "expected_facts appear (case-insensitive substring) in the "
        "concatenated retrieved chunk text? Algorithm matches "
        "eval/scorer.py::_score_faithfulness. Input ports override params."
    ),
    category="eval",
    inputs=[
        Port("results", "results", "Retrieval Results"),
        Port("expected_facts", "string", "Expected Facts"),
        Port("match_mode", "string", "Match Mode"),
    ],
    outputs=[Port("metric", "metric", "Metric")],
    params=[
        ParamDef("expected_facts", "Expected Facts (one per line, fallback)", "textarea", ""),
        ParamDef("match_mode", "Match Mode (fallback)", "select", "all", options=["all", "any"]),
    ],
))

# --- Eval Report ---
_register(NodeType(
    type_id="eval_report",
    label="Eval Report",
    label_en="EvalReport",
    description=(
        "Aggregate up to 4 metric ports into a single markdown summary. "
        "Each input is optional — unwired ports are skipped. Preview is "
        "renderable in a Result Display node downstream."
    ),
    category="eval",
    inputs=[
        Port("coverage", "metric", "Coverage"),
        Port("score_distribution", "metric", "Score Distribution"),
        Port("diversity", "metric", "Diversity"),
        Port("facts_coverage", "metric", "Facts Coverage"),
    ],
    outputs=[Port("answer", "answer", "Report (markdown)")],
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
