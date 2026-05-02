"""
節點執行器。

每個函式對應一種節點類型，接收上游資料 + 參數，回傳執行結果。
所有函式簽名：execute_xxx(inputs: dict, params: dict) -> dict
  - inputs: 上游節點傳來的資料，key = port name
  - params: 使用者設定的參數
  - return: 輸出資料，key = output port name
"""

from config.settings import Settings
from core.loader import load_directory, load_file, load_reference_text
from core.chunker import chunk_document
from core.embedder import embed_chunks
from core.vector_store import get_client, create_collection, add_chunks, delete_collection
from core.retriever import retrieve
from core.prompt_builder import build_prompt
from core.generator import generate
from core.guardrail import (
    GuardrailBlocked,
    check_query as guardrail_check,
    parse_keywords as guardrail_parse_keywords,
)
from core.critic import critique_answer, revise_answer
from core.generator import GenerationResult
from core.personas import get_preset
from core.product_selector import select_product
from core.product_matcher import detect_product_filter, DEFAULT_BRAND_ALIASES
import json


def execute_loader(inputs: dict, params: dict) -> dict:
    """載入檔案或資料夾。"""
    source_path = params.get("source_path", "./knowledge_base")

    if source_path.endswith((".txt", ".md", ".csv", ".pdf")):
        docs = [load_file(source_path)]
    else:
        docs = load_directory(source_path)

    print(f"[Executor:Loader] Loaded {len(docs)} documents from {source_path}")
    return {
        "documents": docs,
        "_preview": f"{len(docs)} 份文件",
    }


def execute_chunker(inputs: dict, params: dict) -> dict:
    """切割文件。"""
    docs = inputs["documents"]
    strategy = params.get("strategy", "section")
    chunk_size = int(params.get("chunk_size", 500))
    chunk_overlap = int(params.get("chunk_overlap", 50))

    all_chunks = []
    for doc in docs:
        file_type = doc.metadata.get("type", "")
        doc_strategy = "csv_row" if file_type == "csv" else strategy
        chunks = chunk_document(
            doc,
            strategy=doc_strategy,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        all_chunks.extend(chunks)

    # Debug: 檢查是否有空 chunk
    empty_count = sum(1 for c in all_chunks if not c.text or not c.text.strip())
    if empty_count:
        print(f"[Executor:Chunker] WARNING: {empty_count} empty chunks detected, filtering out")
        all_chunks = [c for c in all_chunks if c.text and c.text.strip()]

    print(f"[Executor:Chunker] Created {len(all_chunks)} chunks (strategy={strategy})")
    return {
        "chunks": all_chunks,
        "_preview": f"{len(all_chunks)} 個 chunks",
    }


def execute_embedder(inputs: dict, params: dict) -> dict:
    """向量化 chunks。"""
    chunks = [c for c in inputs["chunks"] if c.text and c.text.strip()]
    model = params.get("model", "nomic-embed-text")
    settings = Settings()

    embeddings = embed_chunks(chunks, model=model, base_url=settings.ollama_base_url)

    print(f"[Executor:Embedder] Embedded {len(embeddings)} chunks (model={model})")
    return {
        "embeddings": embeddings,
        "_preview": f"{len(embeddings)} 個向量 (dim={len(embeddings[0]) if embeddings else 0})",
    }


def execute_vectorstore(inputs: dict, params: dict) -> dict:
    """寫入向量資料庫。"""
    chunks = inputs["chunks"]
    embeddings = inputs["embeddings"]
    persist_path = params.get("persist_path", "./chroma_db")
    collection_name = params.get("collection_name", "rag_collection")

    client = get_client(persist_path)
    # Drop existing collection so renamed/removed source files don't leave
    # orphan chunks behind on repeated runs.
    try:
        delete_collection(client, name=collection_name)
    except Exception:
        pass  # collection doesn't exist yet
    collection = create_collection(client, name=collection_name)
    add_chunks(collection, chunks, embeddings)

    print(f"[Executor:VectorStore] Stored {len(chunks)} chunks in '{collection_name}'")
    return {
        "collection": {"client_path": persist_path, "name": collection_name},
        "_preview": f"Collection '{collection_name}' ({len(chunks)} chunks)",
    }


def execute_reference_loader(inputs: dict, params: dict) -> dict:
    """Load always-on reference material (no chunking, no embedding)."""
    source_path = params.get("source_path", "./knowledge_base/_reference")
    text = load_reference_text(source_path)
    print(f"[Executor:ReferenceLoader] Loaded {len(text)} chars from {source_path}")
    preview = (text[:160] + "...") if len(text) > 160 else text
    return {
        "reference_data": text,
        "_preview": f"{len(text)} chars\n{preview}" if text else "(empty)",
    }


def execute_query_input(inputs: dict, params: dict) -> dict:
    """使用者問題輸入。"""
    question = params.get("question", "")
    print(f"[Executor:QueryInput] Question: {question[:50]}...")
    return {
        "query": question,
        "_preview": question[:80] if question else "(empty)",
    }


def execute_guardrail(inputs: dict, params: dict) -> dict:
    """Check query against blocked keywords. Raises GuardrailBlocked if blocked."""
    query = inputs["query_in"]
    keywords = guardrail_parse_keywords(params.get("blocked_keywords", ""))
    refusal = params.get("refusal_message", "") or None

    allowed, message, matched = guardrail_check(
        query,
        blocked_keywords=keywords or None,
        refusal_message=refusal,
    )

    if not allowed:
        print(f"[Executor:Guardrail] BLOCKED (matched '{matched}'): {query[:60]}")
        raise GuardrailBlocked(
            reason=f"Query matched blocked keyword: {matched}",
            refusal_message=message,
            matched_keyword=matched,
        )

    print(f"[Executor:Guardrail] PASS: {query[:60]}")
    return {
        "query_out": query,
        "_preview": f"✓ Passed\n{query[:60]}",
    }


def execute_product_selector(inputs: dict, params: dict) -> dict:
    """Classify a query to a single product_id.

    Two modes:
      - rule (default): string match the query against product_ids derived
        from collection metadata. Zero LLM latency. Needs `collection` input.
      - llm: small LLM pass against a reference table. Needs `reference_data`
        input. Slower but can resolve ambiguous phrasing the rule matcher misses.
    """
    query = inputs.get("query", "") or ""
    mode = params.get("mode", "rule")

    if mode == "rule":
        collection_info = inputs.get("collection")
        if not collection_info:
            return {"product_id": "", "_preview": "(rule mode needs collection input)"}
        client = get_client(collection_info["client_path"])
        collection = create_collection(client, name=collection_info["name"])
        if collection.count() == 0:
            return {"product_id": "", "_preview": "(empty collection — broad search)"}
        meta = collection.get(include=["metadatas"])
        product_ids = {
            m["product_id"]
            for m in meta["metadatas"]
            if m and m.get("product_id")
        }
        aliases_raw = (params.get("aliases") or "").strip()
        if aliases_raw:
            try:
                aliases = json.loads(aliases_raw)
            except json.JSONDecodeError as e:
                print(f"[Executor:ProductSelector] Invalid aliases JSON, using defaults: {e}")
                aliases = DEFAULT_BRAND_ALIASES
        else:
            aliases = DEFAULT_BRAND_ALIASES
        product_id = detect_product_filter(query, product_ids, aliases=aliases) or ""
    else:
        reference_data = inputs.get("reference_data", "") or ""
        settings = Settings()
        product_id = select_product(
            query=query,
            reference_text=reference_data,
            model=params.get("model", "gemma3:4b"),
            base_url=settings.ollama_base_url,
        )

    preview = f"[{mode}] product_id='{product_id}'" if product_id else f"[{mode}] (none — broad search)"
    return {
        "product_id": product_id,
        "_preview": preview,
    }


def execute_retriever(inputs: dict, params: dict) -> dict:
    """檢索相關片段。"""
    query = inputs["query"]
    collection_info = inputs["collection"]
    top_k = int(params.get("top_k", 3))
    score_threshold = float(params.get("score_threshold", 0.0))
    keyword_boost = float(params.get("keyword_boost", 0.3))
    embedding_model = params.get("embedding_model", "nomic-embed-text")
    # Input port overrides the manual param. Upstream ProductSelector wins
    # when connected; otherwise fall back to the user-typed filter.
    port_product_id = (inputs.get("product_id", "") or "").strip()
    param_product_filter = (params.get("product_filter", "") or "").strip()
    product_filter = port_product_id or param_product_filter
    settings = Settings()

    # Re-open collection from persisted path
    client = get_client(collection_info["client_path"])
    collection = create_collection(client, name=collection_info["name"])

    filters = {"product_id": product_filter} if product_filter else None

    results = retrieve(
        query_text=query,
        collection=collection,
        top_k=top_k,
        score_threshold=score_threshold,
        keyword_boost=keyword_boost,
        embedding_model=embedding_model,
        base_url=settings.ollama_base_url,
        filters=filters,
    )

    filter_note = f" [filter={product_filter}]" if product_filter else ""
    print(f"[Executor:Retriever] Found {len(results)} results for query{filter_note}")
    previews = [f"[{r.score:.2f}] {r.chunk.text[:40]}..." for r in results[:3]]
    return {
        "results": results,
        "_preview": f"{len(results)} 筆結果\n" + "\n".join(previews),
    }


def execute_prompt_builder(inputs: dict, params: dict) -> dict:
    """Assemble RAG context into a prompt (no persona)."""
    query = inputs["query"]
    results = inputs["results"]
    glossary = params.get("glossary", "")
    reference_data = inputs.get("reference_data", "") or ""

    prompt = build_prompt(
        query=query,
        contexts=results,
        glossary=glossary,
        reference_data=reference_data,
    )

    print(f"[Executor:PromptBuilder] Built prompt with {len(results)} context(s)")
    system_preview = prompt["system"][:80] if prompt.get("system") else ""
    return {
        "prompt": prompt,
        "_preview": f"context: {system_preview}...",
    }


def execute_system_prompt(inputs: dict, params: dict) -> dict:
    """Resolve persona from preset (or custom textarea) and emit text + format hint."""
    preset_name = params.get("preset", "professional")
    custom_text = params.get("text", "")

    if preset_name == "custom":
        text = custom_text or "You are a helpful assistant."
        format_hint = ""
    else:
        persona = get_preset(preset_name)
        if persona is None:
            raise ValueError(f"Unknown SystemPrompt preset: {preset_name}")
        text = persona.text
        format_hint = persona.format_hint

    print(f"[Executor:SystemPrompt] preset={preset_name} ({len(text)} chars, format_hint={format_hint or 'text'})")
    return {
        "system_prompt": text,
        "format_hint": format_hint,
        "_preview": f"preset={preset_name} | format_hint={format_hint or 'text'}\n{text[:80]}...",
    }


def execute_generator(inputs: dict, params: dict) -> dict:
    """Call Ollama LLM to generate an answer.

    The Generator combines:
      - A SystemPrompt persona (optional input port `system_prompt`)
      - A PromptBuilder context block (input `prompt`, the `system` field)
      - A format hint (optional input port `format_hint`, falls back to manual `format_type` param)
    """
    prompt = inputs["prompt"]
    model = params.get("model", "gemma3:4b")
    format_type_param = params.get("format_type", "")
    settings = Settings()

    # Resolve format: explicit param override > input port hint > plain text
    format_hint = inputs.get("format_hint", "") or ""
    format_type = format_type_param or format_hint

    # Combine persona (if connected) with the context block
    persona_text = inputs.get("system_prompt", "") or ""
    context_block = prompt.get("system", "") or ""
    if persona_text:
        full_system = f"{persona_text}\n\n{context_block}" if context_block else persona_text
    else:
        full_system = context_block

    final_prompt = {**prompt, "system": full_system}

    result = generate(
        prompt=final_prompt,
        model=model,
        format_type=format_type,
        messages=[],
        base_url=settings.ollama_base_url,
    )

    print(f"[Executor:Generator] Generated (model={model}, format={format_type or 'text'}, persona={'yes' if persona_text else 'no'})")
    return {
        "answer": result,
        "_preview": result.text[:200] if result.text else "(empty)",
    }


def execute_output_critic(inputs: dict, params: dict) -> dict:
    """Run a self-critique pass on the generator's answer."""
    answer = inputs["answer_in"]
    if answer is None:
        return {"answer_out": None, "_preview": "(no input)"}

    criteria = params.get("criteria", "").strip()
    mode = params.get("mode", "audit")
    model = params.get("model", "gemma3:4b")
    settings = Settings()

    original_text = answer.text if hasattr(answer, "text") else str(answer)

    if not criteria:
        # Nothing to critique against — pass through with a neutral verdict
        preview = json.dumps({"__critic": True, "verdict": "skip", "reason": "No criteria configured.", "revised": False})
        return {"answer_out": answer, "_preview": preview}

    verdict = critique_answer(
        answer_text=original_text,
        criteria=criteria,
        model=model,
        base_url=settings.ollama_base_url,
    )

    revised = False
    final_text = original_text

    if not verdict.passed and mode == "revise":
        print(f"[Executor:OutputCritic] FAIL → revising. Reason: {verdict.reason}")
        final_text = revise_answer(
            original_text=original_text,
            criteria=criteria,
            critique_reason=verdict.reason,
            model=model,
            base_url=settings.ollama_base_url,
        )
        revised = True
    else:
        print(f"[Executor:OutputCritic] {'PASS' if verdict.passed else 'FAIL (audit only)'}: {verdict.reason}")

    # Wrap the (possibly revised) text back into a GenerationResult
    new_answer = GenerationResult(
        text=final_text,
        messages=answer.messages if hasattr(answer, "messages") else [],
        model=answer.model if hasattr(answer, "model") else model,
    )

    preview_obj = {
        "__critic": True,
        "verdict": "pass" if verdict.passed else "fail",
        "reason": verdict.reason,
        "revised": revised,
        "mode": mode,
    }
    return {
        "answer_out": new_answer,
        "_preview": json.dumps(preview_obj),
    }


def execute_result_display(inputs: dict, params: dict) -> dict:
    """顯示最終結果。"""
    answer = inputs.get("answer")
    if answer is None:
        return {"_preview": "(no input)"}

    # answer 是 GenerationResult dataclass
    text = answer.text if hasattr(answer, "text") else str(answer)
    print(f"[Executor:ResultDisplay] Output length: {len(text)} chars")
    return {
        "_preview": text,
    }


# ── Executor Registry ──────────────────────────────────────────────

EXECUTORS: dict[str, callable] = {
    "loader": execute_loader,
    "chunker": execute_chunker,
    "embedder": execute_embedder,
    "vectorstore": execute_vectorstore,
    "reference_loader": execute_reference_loader,
    "query_input": execute_query_input,
    "guardrail": execute_guardrail,
    "product_selector": execute_product_selector,
    "retriever": execute_retriever,
    "prompt_builder": execute_prompt_builder,
    "system_prompt": execute_system_prompt,
    "generator": execute_generator,
    "output_critic": execute_output_critic,
    "result_display": execute_result_display,
}
