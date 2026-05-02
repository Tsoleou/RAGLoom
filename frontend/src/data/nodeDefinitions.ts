import type { NodeTypeDef } from "../types/pipeline";

/**
 * Static node definitions — mirrors api/node_registry.py.
 * Can also be fetched dynamically via GET /api/node-types.
 */
export const NODE_DEFINITIONS: NodeTypeDef[] = [
  {
    typeId: "loader",
    label: "Loader",
    labelEn: "Loader",
    description: "Load files or directories into Document objects.",
    category: "ingest",
    inputs: [],
    outputs: [{ name: "documents", dataType: "documents", label: "Documents" }],
    params: [
      { name: "source_path", label: "Source Path", type: "string", default: "./knowledge_base" },
    ],
  },
  {
    typeId: "chunker",
    label: "Chunker",
    labelEn: "Chunker",
    description: "Split documents into smaller chunks.",
    category: "ingest",
    inputs: [{ name: "documents", dataType: "documents", label: "Documents" }],
    outputs: [{ name: "chunks", dataType: "chunks", label: "Chunks" }],
    params: [
      { name: "strategy", label: "Strategy", type: "select", default: "section", options: ["section", "csv_row", "fixed"] },
      { name: "chunk_size", label: "Chunk Size", type: "number", default: 500 },
      { name: "chunk_overlap", label: "Overlap", type: "number", default: 50 },
    ],
  },
  {
    typeId: "embedder",
    label: "Embedder",
    labelEn: "Embedder",
    description: "Convert text to vectors via Ollama embedding models.",
    category: "shared",
    inputs: [{ name: "chunks", dataType: "chunks", label: "Chunks" }],
    outputs: [{ name: "embeddings", dataType: "embeddings", label: "Embeddings" }],
    params: [
      { name: "model", label: "Model", type: "string", default: "nomic-embed-text" },
    ],
  },
  {
    typeId: "vectorstore",
    label: "Vector Store",
    labelEn: "VectorStore",
    description: "Store chunks and embeddings in ChromaDB.",
    category: "ingest",
    inputs: [
      { name: "chunks", dataType: "chunks", label: "Chunks" },
      { name: "embeddings", dataType: "embeddings", label: "Embeddings" },
    ],
    outputs: [{ name: "collection", dataType: "collection", label: "Collection" }],
    params: [
      { name: "persist_path", label: "Persist Path", type: "string", default: "./chroma_db" },
      { name: "collection_name", label: "Collection Name", type: "string", default: "rag_collection" },
    ],
  },
  {
    typeId: "reference_loader",
    label: "Reference Loader",
    labelEn: "ReferenceLoader",
    description: "Load a file or directory as always-on reference material (no chunking, no vector store). Use for small comparison tables / pricing sheets that the LLM should always see.",
    category: "query",
    inputs: [],
    outputs: [{ name: "reference_data", dataType: "reference", label: "Reference Data" }],
    params: [
      { name: "source_path", label: "Source Path", type: "string", default: "./knowledge_base/_reference" },
    ],
  },
  {
    typeId: "query_input",
    label: "Query Input",
    labelEn: "QueryInput",
    description: "Enter the question to query.",
    category: "query",
    inputs: [],
    outputs: [{ name: "query", dataType: "query", label: "Query Text" }],
    params: [
      { name: "question", label: "Question", type: "string", default: "" },
    ],
  },
  {
    typeId: "guardrail",
    label: "Guardrail",
    labelEn: "Guardrail",
    description: "Block queries containing restricted keywords (e.g., competitor brands). If blocked, short-circuits the pipeline with a refusal message.",
    category: "query",
    inputs: [{ name: "query_in", dataType: "query", label: "Query Text" }],
    outputs: [{ name: "query_out", dataType: "query", label: "Query Text" }],
    params: [
      { name: "blocked_keywords", label: "Blocked Keywords", type: "string", default: "asus, acer, msi, hp, dell, apple" },
      {
        name: "refusal_message",
        label: "Refusal Message",
        type: "textarea",
        default:
          "I'm sorry, but I can only answer questions about our own products. " +
          "For information about other brands, please visit their official channels.",
      },
    ],
  },
  {
    typeId: "scope_gate",
    label: "Scope Gate",
    labelEn: "ScopeGate",
    description:
      "Block off-topic queries with a semantic-relevance check. Two modes: " +
      "'semantic' (default) compares the query embedding against on/off-topic " +
      "anchor phrases that live outside the KB — robust to bridge attacks. " +
      "'retrieval' thresholds the top retrieval score (cheaper, but vulnerable " +
      "when KB tokens are background noise). Greetings and very short queries " +
      "bypass either mode. Short-circuits the pipeline with a language-aware refusal.",
    category: "query",
    inputs: [
      { name: "results_in", dataType: "results", label: "RetrievalResults" },
      { name: "query", dataType: "query", label: "Query Text" },
    ],
    outputs: [{ name: "results_out", dataType: "results", label: "RetrievalResults" }],
    params: [
      { name: "mode", label: "Mode", type: "select", default: "semantic", options: ["semantic", "retrieval"] },
      {
        name: "on_topic_anchors",
        label: "On-Topic Anchors (semantic mode, one per line)",
        type: "textarea",
        default: [
          "Questions about laptop computers, their specs, prices, or features",
          "Asking which laptop is best for gaming, work, school, or creative use",
          "Comparing laptop products across brands or models",
          "Questions about laptop hardware: CPU, GPU, RAM, screen, battery, or weight",
          "Questions about specific laptop models, brands, or product lines",
          "筆記型電腦的規格、價格、功能或推薦",
          "詢問筆電的處理器、顯示卡、記憶體、螢幕等硬體",
          "詢問哪一款筆電適合特定用途",
        ].join("\n"),
      },
      {
        name: "off_topic_anchors",
        label: "Off-Topic Anchors (semantic mode, one per line)",
        type: "textarea",
        default: [
          "Questions about pets, animals, or breeds",
          "Questions about food, cooking, or restaurants",
          "Questions about movies, music, sports, or entertainment",
          "Questions about weather, news, or current events",
          "關於寵物、動物、食物、天氣的問題",
          "與電腦科技無關的個人生活建議",
        ].join("\n"),
      },
      { name: "margin_threshold", label: "Margin Threshold (semantic mode)", type: "number", default: 0.0 },
      { name: "min_score", label: "Min Retrieval Score (retrieval mode)", type: "number", default: 0.7 },
      { name: "embedding_model", label: "Embedding Model (semantic mode)", type: "string", default: "nomic-embed-text" },
    ],
  },
  {
    typeId: "product_selector",
    label: "Product Selector",
    labelEn: "ProductSelector",
    description:
      "Classify the query to a single product_id and feed it into Retriever to scope retrieval. " +
      "Two modes: 'rule' uses fast string matching against product_ids in the collection (zero LLM latency, " +
      "needs the collection input). 'llm' uses a small LLM pass against a product reference table " +
      "(needs the reference_data input). Empty output means no clear match — Retriever falls back to broad search.",
    category: "query",
    inputs: [
      { name: "query", dataType: "query", label: "Query Text" },
      { name: "collection", dataType: "collection", label: "Collection" },
      { name: "reference_data", dataType: "reference", label: "Reference Data" },
    ],
    outputs: [{ name: "product_id", dataType: "product_id", label: "Product ID" }],
    params: [
      { name: "mode", label: "Mode", type: "select", default: "rule", options: ["rule", "llm"] },
      { name: "model", label: "Model (LLM mode)", type: "string", default: "gemma3:4b" },
      {
        name: "aliases",
        label: "Brand Aliases (JSON, rule mode)",
        type: "textarea",
        default: JSON.stringify(
          {
            starforge: ["星鋒", "星峰"],
            visionbook: ["維森書", "視覺書"],
            novapad: ["諾瓦", "諾瓦帕"],
            titanbook: ["泰坦書", "鈦書"],
            luminos: ["璐米諾", "流明"],
          },
          null,
          2,
        ),
      },
    ],
  },
  {
    typeId: "retriever",
    label: "Retriever",
    labelEn: "Retriever",
    description: "Retrieve relevant chunks from the vector store.",
    category: "query",
    inputs: [
      { name: "query", dataType: "query", label: "Query Text" },
      { name: "collection", dataType: "collection", label: "Collection" },
      { name: "product_id", dataType: "product_id", label: "Product ID" },
    ],
    outputs: [{ name: "results", dataType: "results", label: "RetrievalResults" }],
    params: [
      { name: "top_k", label: "Top K", type: "number", default: 3 },
      { name: "score_threshold", label: "Score Threshold", type: "number", default: 0.0 },
      { name: "keyword_boost", label: "Keyword Boost", type: "number", default: 0.3 },
      { name: "embedding_model", label: "Embedding Model", type: "string", default: "nomic-embed-text" },
      { name: "product_filter", label: "Product Filter", type: "string", default: "" },
    ],
  },
  {
    typeId: "prompt_builder",
    label: "Prompt Builder",
    labelEn: "PromptBuilder",
    description: "Assemble retrieval results into a context-only prompt. Persona lives on SystemPrompt, format on Generator.",
    category: "query",
    inputs: [
      { name: "query", dataType: "query", label: "Query Text" },
      { name: "results", dataType: "results", label: "RetrievalResults" },
      { name: "reference_data", dataType: "reference", label: "Reference Data" },
    ],
    outputs: [{ name: "prompt", dataType: "prompt", label: "Prompt" }],
    params: [
      { name: "glossary", label: "Glossary", type: "string", default: "" },
    ],
  },
  {
    typeId: "system_prompt",
    label: "System Prompt",
    labelEn: "SystemPrompt",
    description: "Defines persona/tone via a preset (professional / chatbot / custom). Outputs persona text + a format hint for Generator.",
    category: "query",
    inputs: [],
    outputs: [
      { name: "system_prompt", dataType: "system_prompt", label: "System Prompt" },
      { name: "format_hint", dataType: "format_hint", label: "Format Hint" },
    ],
    params: [
      {
        name: "preset",
        label: "Preset",
        type: "select",
        default: "professional",
        options: ["professional", "chatbot", "custom"],
      },
      {
        name: "text",
        label: "Custom Text",
        type: "textarea",
        default:
          "You are a product specialist for a PC manufacturer, helping customers at a live demo station.\n\n" +
          "RULES:\n" +
          "1. Answer ONLY using facts from [Internal Knowledge]. Never fabricate specs, prices, or model names.\n" +
          "2. If the knowledge base doesn't contain the answer, say so honestly and suggest what you can help with instead.\n" +
          "3. Keep answers concise (2-4 sentences) — customers are standing at a demo booth, not reading a manual.\n" +
          "4. Match the user's language (English, 繁體中文, etc.).\n" +
          "5. Tone: Professional, confident, approachable. No marketing fluff.",
      },
    ],
  },
  {
    typeId: "generator",
    label: "Generator",
    labelEn: "Generator",
    description: "Call Ollama LLM to generate an answer. Optionally takes a SystemPrompt persona and a format hint.",
    category: "query",
    inputs: [
      { name: "prompt", dataType: "prompt", label: "Prompt" },
      { name: "system_prompt", dataType: "system_prompt", label: "System Prompt" },
      { name: "format_hint", dataType: "format_hint", label: "Format Hint" },
    ],
    outputs: [{ name: "answer", dataType: "answer", label: "Answer" }],
    params: [
      { name: "model", label: "Model", type: "string", default: "gemma3:4b" },
      { name: "format_type", label: "Format Override", type: "select", default: "", options: ["", "json"] },
    ],
  },
  {
    typeId: "output_critic",
    label: "Output Critic",
    labelEn: "OutputCritic",
    description: "Run a second LLM pass to check the answer against negative rules. Can audit (label) or revise (rewrite).",
    category: "query",
    inputs: [{ name: "answer_in", dataType: "answer", label: "Answer" }],
    outputs: [{ name: "answer_out", dataType: "answer", label: "Answer" }],
    params: [
      {
        name: "criteria",
        label: "Negative Rules",
        type: "textarea",
        default:
          "Do not mention competitor brand names like Asus, Acer, MSI, HP, Dell, or Apple.\n" +
          "Do not promise specific pricing, availability, or release dates.\n" +
          "Do not invent technical specifications not present in the source material.\n" +
          'Do not use marketing buzzwords like "amazing", "revolutionary", "industry-leading", "best-in-class".',
      },
      { name: "mode", label: "Mode", type: "select", default: "audit", options: ["audit", "revise"] },
      { name: "model", label: "Model", type: "string", default: "gemma3:4b" },
    ],
  },
  {
    typeId: "result_display",
    label: "Result Display",
    labelEn: "ResultDisplay",
    description: "Display the final generated answer.",
    category: "query",
    inputs: [{ name: "answer", dataType: "answer", label: "Answer" }],
    outputs: [],
    params: [],
  },
];

/** Quick lookup by typeId */
export const NODE_DEF_MAP = Object.fromEntries(
  NODE_DEFINITIONS.map((d) => [d.typeId, d])
);
