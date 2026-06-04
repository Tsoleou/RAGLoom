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
from core.path_guard import safe_path as _safe_path

# Path guard 共用 Settings 一次（避免每次節點執行都重讀 .env）。允許的根目錄
# 來自 RAG_ALLOWED_DATA_ROOTS env 或預設 ./knowledge_base、./eval、./chroma_db。
_PATH_GUARD_SETTINGS: Settings | None = None


def _allowed_roots() -> list[str]:
    global _PATH_GUARD_SETTINGS
    if _PATH_GUARD_SETTINGS is None:
        _PATH_GUARD_SETTINGS = Settings.from_env()
    return _PATH_GUARD_SETTINGS.allowed_data_roots


def _guard_path(raw: str, kind: str) -> str:
    return str(_safe_path(raw, allowed_roots=_allowed_roots(), kind=kind))
from core.chunker import chunk_document
from core.embedder import embed_chunks
from core.vector_store import get_client, create_collection, add_chunks, delete_collection
from core.retriever import retrieve
from core.prompt_builder import build_prompt
from core.generator import generate
from core.guardrail import (
    GuardrailBlocked,
    check_query as guardrail_check,
    format_refusal as guardrail_format_refusal,
    parse_keywords as guardrail_parse_keywords,
)
from core.scope_gate import (
    ScopeBlocked,
    check_scope,
    check_scope_semantic,
    refusal_message as scope_refusal_message,
)
from core.price_guard import (
    PriceGuardBlocked,
    is_price_query,
    refusal_message as price_refusal_message,
)
from core.retrieval_judge import judge_retrieval
from core.constraint_filter import (
    ConstraintBlocked,
    extract_constraints,
    build_spec_table,
    filter_results as constraint_filter_results,
    filter_reference_rows,
    any_product_matches,
    refusal_message as constraint_refusal_message,
)
from core.critic import critique_answer, revise_answer
from core.generator import GenerationResult
from core.personas import get_preset
from core.product_selector import select_product
from core.eval_metrics import (
    compute_coverage,
    compute_score_distribution,
    compute_diversity,
    compute_facts_coverage,
)
from pathlib import Path
from core.product_matcher import detect_product_filter, DEFAULT_BRAND_ALIASES
import json


def execute_loader(inputs: dict, params: dict) -> dict:
    """載入檔案或資料夾。"""
    source_path = _guard_path(
        params.get("source_path", "./knowledge_base"), kind="loader source_path"
    )

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
    """寫入向量資料庫。

    `wipe_collection` 預設為 False — 重跑 Editor pipeline 時，**不會**砍掉
    現有 collection。重複 ID 走 upsert 直接覆寫，新 ID 插入，舊資料保留。
    這避免了在 Editor 試跑就把 ChatView 已經 ingest 的庫炸掉的雷。
    需要徹底重建（例如改 chunker 策略、刪檔案）時把這個 param 打開。
    """
    chunks = inputs["chunks"]
    embeddings = inputs["embeddings"]
    persist_path = _guard_path(
        params.get("persist_path", "./chroma_db"), kind="vectorstore persist_path"
    )
    collection_name = params.get("collection_name", "rag_collection")
    wipe_collection = bool(params.get("wipe_collection", False))

    client = get_client(persist_path)
    if wipe_collection:
        try:
            delete_collection(client, name=collection_name)
        except Exception:
            pass  # collection doesn't exist yet
        collection = create_collection(client, name=collection_name)
        add_chunks(collection, chunks, embeddings, upsert=False)
        action = "rebuilt (wiped + reloaded)"
    else:
        collection = create_collection(client, name=collection_name)
        add_chunks(collection, chunks, embeddings, upsert=True)
        action = "upserted (preserved existing)"

    print(f"[Executor:VectorStore] {action}: {len(chunks)} chunks in '{collection_name}'")
    return {
        "collection": {"client_path": persist_path, "name": collection_name},
        "_preview": f"Collection '{collection_name}' — {action}\n{len(chunks)} chunks (total: {collection.count()})",
    }


def execute_reference_loader(inputs: dict, params: dict) -> dict:
    """Load always-on reference material (no chunking, no embedding)."""
    source_path = _guard_path(
        params.get("source_path", "./knowledge_base/_reference"),
        kind="reference_loader source_path",
    )
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
    format_hint = inputs.get("format_hint")
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
            refusal_message=guardrail_format_refusal(message, format_hint=format_hint),
            matched_keyword=matched,
        )

    print(f"[Executor:Guardrail] PASS: {query[:60]}")
    return {
        "query_out": query,
        "_preview": f"✓ Passed\n{query[:60]}",
    }


def execute_price_guard(inputs: dict, params: dict) -> dict:
    """Block queries asking about price / cost / discount.

    gemma3:4b fabricates dollar amounts under direct pricing pressure even
    when the persona forbids it (verified 2026-05-12). The KB has zero price
    data, so detecting the intent in code and refusing here is more reliable
    than trusting prompt rules. Mirrors Guardrail's shape: passes query
    through on success, raises PriceGuardBlocked on hit.
    """
    query = inputs["query_in"]
    format_hint = inputs.get("format_hint")

    if is_price_query(query):
        message = price_refusal_message(query, format_hint=format_hint)
        print(f"[Executor:PriceGuard] BLOCKED price intent: {query[:60]}")
        raise PriceGuardBlocked(
            reason="Query contains pricing intent",
            refusal_message=message,
        )

    print(f"[Executor:PriceGuard] PASS: {query[:60]}")
    return {
        "query_out": query,
        "_preview": f"✓ Passed\n{query[:60]}",
    }


def execute_scope_gate(inputs: dict, params: dict) -> dict:
    """Block off-topic queries; pass on-topic results through unchanged.

    Two modes — 'semantic' (default) uses anchor-phrase embeddings; 'retrieval'
    thresholds the top retrieval score. Greetings and very short queries bypass
    either mode. Off-topic queries raise ScopeBlocked which the engine handles
    like GuardrailBlocked.
    """
    results = inputs["results_in"]
    query = inputs.get("query", "") or ""
    format_hint = inputs.get("format_hint")
    mode = params.get("mode", "semantic")

    if mode == "semantic":
        on_anchors = [a for a in (params.get("on_topic_anchors", "") or "").splitlines() if a.strip()]
        off_anchors = [a for a in (params.get("off_topic_anchors", "") or "").splitlines() if a.strip()]
        try:
            margin_threshold = float(params.get("margin_threshold", 0.0))
        except (TypeError, ValueError):
            margin_threshold = 0.0
        embedding_model = params.get("embedding_model", "nomic-embed-text")
        settings = Settings()

        allowed, margin = check_scope_semantic(
            query,
            on_topic_anchors=on_anchors or None,
            off_topic_anchors=off_anchors or None,
            margin_threshold=margin_threshold,
            embedding_model=embedding_model,
            base_url=settings.ollama_base_url,
        )

        if not allowed:
            message = scope_refusal_message(query, format_hint=format_hint)
            print(f"[Executor:ScopeGate] BLOCKED semantic (margin={margin:+.3f}): {query[:60]}")
            raise ScopeBlocked(
                reason=f"Semantic margin {margin:+.3f} below threshold {margin_threshold}",
                refusal_message=message,
                max_score=margin,
            )

        print(f"[Executor:ScopeGate] PASS semantic (margin={margin:+.3f}): {query[:60]}")
        return {
            "results_out": results,
            "_preview": f"✓ Passed (semantic margin={margin:+.3f})",
        }

    # mode == "retrieval"
    try:
        min_score = float(params.get("min_score", 0.7))
    except (TypeError, ValueError):
        min_score = 0.7

    allowed, max_score = check_scope(query, results, min_score=min_score)

    if not allowed:
        message = scope_refusal_message(query, format_hint=format_hint)
        print(f"[Executor:ScopeGate] BLOCKED retrieval (max_score={max_score:.2f} < {min_score}): {query[:60]}")
        raise ScopeBlocked(
            reason=f"Top retrieval score {max_score:.2f} below threshold {min_score}",
            refusal_message=message,
            max_score=max_score,
        )

    print(f"[Executor:ScopeGate] PASS retrieval (max_score={max_score:.2f}): {query[:60]}")
    return {
        "results_out": results,
        "_preview": f"✓ Passed (max_score={max_score:.2f})",
    }


def execute_retrieval_judge(inputs: dict, params: dict) -> dict:
    """LLM-as-judge rerank: drops retrieved chunks that don't actually answer
    the query. Catches negation / polarity flips that cosine similarity misses.

    One batched LLM call per query (regardless of K). On any judge failure
    we degrade to "keep everything" so a flaky judge can't hide good chunks.
    """
    query = inputs.get("query", "") or ""
    candidates = inputs.get("results_in") or []
    model = params.get("model", "gemma3:4b")
    settings = Settings()

    kept, verdicts = judge_retrieval(
        query=query,
        results=candidates,
        model=model,
        base_url=settings.ollama_base_url,
    )

    # Serialize verdicts for both downstream nodes and the chat panel.
    trace = [
        {
            "i": v.index,
            "keep": v.keep,
            "reason": v.reason,
            "source": v.source,
            "score": round(v.score, 4),
        }
        for v in verdicts
    ]
    preview_payload = {"__rerank": True, "kept": len(kept), "total": len(candidates), "verdicts": trace}
    return {
        "results_out": kept,
        "judge_trace": trace,
        "_preview": json.dumps(preview_payload, ensure_ascii=False),
    }


def execute_constraint_filter(inputs: dict, params: dict) -> dict:
    """Drop retrieved chunks (and reference rows) whose product violates a
    numeric constraint extracted from the query. Deterministic, no LLM.

    No-op (pass-through) when the query states no numeric constraint, or when
    no reference table is wired (can't build a spec table → nothing to compare).
    """
    query = inputs.get("query", "") or ""
    candidates = inputs.get("results_in") or []
    reference_data = inputs.get("reference_in", "") or ""
    format_hint = inputs.get("format_hint")

    constraints = extract_constraints(query)
    if not constraints:
        preview = json.dumps({"__constraint": True, "constraints": [], "note": "no numeric constraint"})
        return {"results_out": candidates, "reference_out": reference_data, "_preview": preview}

    spec_table = build_spec_table(reference_data)

    # Catalog-scoped "no match" check: refuse only when NO product in the whole
    # catalog satisfies the constraint. The message claims "we have none", so the
    # check must be catalog-wide — a retrieval-scoped check would falsely refuse
    # when retrieval merely missed the qualifying product. Don't hand the
    # generator a product-less context either way (4B invents a fake product).
    if not any_product_matches(constraints, spec_table):
        descs = ", ".join(c.describe() for c in constraints)
        print(f"[Executor:ConstraintFilter] BLOCKED — no product matches {descs}")
        raise ConstraintBlocked(
            reason=f"No product matches {descs}",
            refusal_message=constraint_refusal_message(query, format_hint=format_hint),
            matched_keyword=f"{descs} (no match)",
        )

    kept, trace = constraint_filter_results(candidates, constraints, spec_table)
    filtered_ref = filter_reference_rows(reference_data, constraints, spec_table)

    preview_payload = {
        "__constraint": True,
        "constraints": [c.describe() for c in constraints],
        "kept": len(kept),
        "total": len(candidates),
        "trace": trace,
    }
    print(f"[Executor:ConstraintFilter] {[c.describe() for c in constraints]} "
          f"→ kept {len(kept)}/{len(candidates)} chunks")
    return {
        "results_out": kept,
        "reference_out": filtered_ref,
        "_preview": json.dumps(preview_payload, ensure_ascii=False),
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

    if isinstance(format_hint, dict):
        format_label = "schema"
    else:
        format_label = format_hint or "text"
    print(f"[Executor:SystemPrompt] preset={preset_name} ({len(text)} chars, format_hint={format_label})")
    return {
        "system_prompt": text,
        "format_hint": format_hint,
        "_preview": f"preset={preset_name} | format_hint={format_label}\n{text[:80]}...",
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
        # Multi-turn history arrives via input_overrides when the caller
        # supplies it — the chat path injects conversation history; the
        # editor path injects nothing and stays single-shot.
        messages=inputs.get("messages") or [],
        base_url=settings.ollama_base_url,
    )

    if isinstance(format_type, dict):
        format_label = "schema"
    else:
        format_label = format_type or "text"
    print(f"[Executor:Generator] Generated (model={model}, format={format_label}, persona={'yes' if persona_text else 'no'})")
    return {
        "answer": result,
        "_preview": result.text[:200] if result.text else "(empty)",
    }


def _parse_chatbot_envelope(text: str) -> dict | None:
    """If `text` is a chatbot {"reply", "emotion"} JSON envelope, return it; else None.

    Mirrors the frontend's parseChatbotOutput so a revise pass can rewrite only
    the inner reply prose and re-wrap with the original emotion — otherwise
    revise_answer (told to emit plain text) would strip the envelope and the
    UI would lose the avatar emotion. Brace-find tolerates leading/trailing
    text around the JSON, same as the frontend's regex.
    """
    if not text:
        return None
    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict) and isinstance(obj.get("reply"), str) and isinstance(obj.get("emotion"), str):
            return {"reply": obj["reply"], "emotion": obj["emotion"]}
    return None


# Generic English spec words that show up in product sheets but aren't a
# product *identity* — excluded so they don't count as "the product" when
# detecting whether a revise pass gutted the answer.
_ANCHOR_STOPWORDS = {
    "core", "cpu", "gpu", "ram", "ddr", "ssd", "wifi", "intel", "nvidia",
    "amd", "geforce", "radeon", "display", "specs", "product", "sheet",
    "internal", "comparison", "reference",
}


def _product_anchors(retrieval_results: list) -> set:
    """Product-identity tokens derived from the retrieved chunks' filenames.

    e.g. 'product_starforge_titan_9000.txt' -> {'starforge', 'titan'}. KB-agnostic:
    a "product" is whatever retrieval surfaced, not a hardcoded brand list.
    Digits, short tokens, and generic spec words are dropped.
    """
    anchors = set()
    for r in retrieval_results:
        chunk = getattr(r, "chunk", None)
        fname = (chunk.metadata.get("filename", "") if chunk is not None else "") or ""
        stem = fname.rsplit(".", 1)[0]
        for tok in stem.replace("-", "_").split("_"):
            t = tok.strip().lower()
            if len(t) >= 4 and t.isalpha() and t not in _ANCHOR_STOPWORDS:
                anchors.add(t)
    return anchors


def _lost_product(original: str, revised: str, anchors: set) -> bool:
    """True if `original` named a retrieved product but `revised` named none —
    i.e. the revise pass gutted the answer down to generic filler."""
    if not anchors:
        return False
    o, rv = original.lower(), revised.lower()
    return any(a in o for a in anchors) and not any(a in rv for a in anchors)


def _regenerate_grounded(query, context_text, reference_data, persona_text,
                         format_hint, model, base_url):
    """Re-run the generator with a strict grounded instruction — the fallback
    when a revise gutted the answer. Reuses core.generator.generate (no parallel
    generation logic). Returns the new text, or None if empty."""
    instruction = (
        "Answer the user's question using ONLY the facts in the context and "
        "reference below. Recommend the most suitable product(s) that appear "
        "there, by name. Do NOT invent specs, model numbers, or products not "
        "present. If the sources lack the detail asked for, say so plainly "
        "instead of inventing. Reply in the user's language."
    )
    grounding = []
    if context_text.strip():
        grounding.append(f"[Context]\n{context_text.strip()}")
    if reference_data.strip():
        grounding.append(f"[Reference]\n{reference_data.strip()}")
    system = "\n\n".join(p for p in (persona_text.strip(), instruction, "\n\n".join(grounding)) if p)
    result = generate(
        prompt={"system": system, "user": query},
        model=model,
        format_type=format_hint or "",
        messages=[],
        base_url=base_url,
    )
    return result.text or None


def _safe_fallback_text(query: str) -> str:
    """Honest canned line when revise + regenerate both fail — never serve an
    unverified answer. Speaks the visitor's language (EN / ZH)."""
    is_zh = any("一" <= ch <= "鿿" for ch in (query or ""))
    if is_zh:
        return "這個問題我手邊的資料不足以給你準確答案，建議直接洽詢現場人員以取得正確資訊。"
    return ("I don't have enough verified information to answer that accurately — "
            "please check with our staff for the correct details.")


def execute_output_critic(inputs: dict, params: dict) -> dict:
    """Run a self-critique pass on the generator's answer.

    Two visibility levels — gated by which input ports are wired:
      - **Rules-only** (legacy): only `answer_in` connected. Checks the answer
        against the negative-rules `criteria`. Can't catch hallucinations or
        off-target answers — it doesn't know what was asked or retrieved.
      - **Grounded**: when `query` and/or `retrieval` are also wired, the
        critic additionally checks that the answer addresses the question and
        is grounded in the retrieved context. Catches hallucinated specs and
        off-target answers that rule checks alone miss.
    """
    answer = inputs["answer_in"]
    if answer is None:
        return {"answer_out": None, "_preview": "(no input)"}

    criteria = params.get("criteria", "").strip()
    mode = params.get("mode", "audit")
    model = params.get("model", "gemma3:4b")
    settings = Settings()

    original_text = answer.text if hasattr(answer, "text") else str(answer)
    # If the answer is a chatbot {"reply","emotion"} envelope, remember its
    # emotion so every downstream rewrite (revise / regen / fallback) can keep it.
    envelope = _parse_chatbot_envelope(original_text)
    original_emotion = envelope["emotion"] if envelope else None

    # Optional grounded-mode inputs
    query = (inputs.get("query") or "") or ""
    retrieval_results = inputs.get("retrieval") or []
    reference_data = (inputs.get("reference_data") or "") or ""
    # Optional — wired from a SystemPrompt node; absent on saved profiles /
    # editor graphs without the edge, so default safely.
    persona_text = inputs.get("system_prompt", "") or ""
    format_hint = inputs.get("format_hint", "") or ""
    context_text = ""
    if retrieval_results:
        # Cap each chunk and the total to keep the prompt small.
        chunk_blocks = []
        for i, r in enumerate(retrieval_results):
            preview = (r.chunk.text if hasattr(r, "chunk") else "")[:400]
            source = r.chunk.metadata.get("filename", "?") if hasattr(r, "chunk") else "?"
            chunk_blocks.append(f"[{i}] (source={source})\n{preview}")
        context_text = "\n\n".join(chunk_blocks)

    grounded = bool(query.strip() or context_text.strip() or reference_data.strip())

    if not criteria and not grounded:
        # Nothing to critique against — pass through with a neutral verdict
        preview = json.dumps({"__critic": True, "verdict": "skip", "reason": "No criteria or grounding inputs configured.", "revised": False, "grounded": False})
        return {"answer_out": answer, "_preview": preview}

    verdict = critique_answer(
        answer_text=original_text,
        criteria=criteria,
        model=model,
        base_url=settings.ollama_base_url,
        query=query,
        context=context_text,
        reference=reference_data,
    )

    revised = False
    regenerated = False
    fell_back = False
    final_text = original_text

    if not verdict.passed and mode in ("revise", "revise+regen"):
        print(f"[Executor:OutputCritic] FAIL → revising ({mode}). Reason: {verdict.reason}")
        # Step 1 — revise. revise_answer emits plain text, so for a chatbot
        # envelope we rewrite only the inner reply prose and re-wrap with the
        # original emotion (else the envelope/emotion is lost).
        if envelope is not None:
            new_reply = revise_answer(
                original_text=envelope["reply"], criteria=criteria,
                critique_reason=verdict.reason, model=model,
                base_url=settings.ollama_base_url,
            )
            final_text = json.dumps({"reply": new_reply, "emotion": original_emotion}, ensure_ascii=False)
        else:
            final_text = revise_answer(
                original_text=original_text, criteria=criteria,
                critique_reason=verdict.reason, model=model,
                base_url=settings.ollama_base_url,
            )
        revised = True

        # Step 2 — "revise+regen" only: if the revise gutted the answer (named
        # a retrieved product before, none after), regenerate a grounded answer
        # instead of serving generic filler. Plain "revise" stops at Step 1.
        if mode == "revise+regen":
            anchors = _product_anchors(retrieval_results)
            if _lost_product(original_text, final_text, anchors):
                print(f"[Executor:OutputCritic] revise dropped all products {sorted(anchors)} → regenerating")
                regen = _regenerate_grounded(
                    query, context_text, reference_data, persona_text,
                    format_hint, model, settings.ollama_base_url,
                )
                if regen:
                    # Step 3 — verify the regen. It uses the same model that just
                    # hallucinated, so an UNVERIFIED regen would be a hallucination
                    # bypass; re-run the same critique before trusting it.
                    regen_verdict = critique_answer(
                        answer_text=regen, criteria=criteria, model=model,
                        base_url=settings.ollama_base_url, query=query,
                        context=context_text, reference=reference_data,
                    )
                    if regen_verdict.passed:
                        final_text = regen
                        regenerated = True
                    else:
                        print(f"[Executor:OutputCritic] regen still fails ({regen_verdict.reason}) → safe fallback")
                        final_text = _safe_fallback_text(query)
                        fell_back = True
                else:
                    final_text = _safe_fallback_text(query)
                    fell_back = True
    else:
        print(f"[Executor:OutputCritic] {'PASS' if verdict.passed else 'FAIL (audit only)'}: {verdict.reason} [grounded={grounded}]")

    # Envelope guard: if the original was a chatbot envelope, ensure the final
    # text still is one. A regen without a format_hint, or the canned fallback,
    # would otherwise be plain text and the UI would lose the emotion.
    if envelope is not None and _parse_chatbot_envelope(final_text) is None:
        final_text = json.dumps({"reply": final_text, "emotion": original_emotion}, ensure_ascii=False)

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
        "regenerated": regenerated,
        "fallback": fell_back,
        "mode": mode,
        "grounded": grounded,
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

    # answer 是 GenerationResult dataclass — 但也可能是字串（eval_report 等節點）
    if hasattr(answer, "text"):
        text = answer.text
    elif isinstance(answer, str):
        text = answer
    else:
        text = str(answer)
    print(f"[Executor:ResultDisplay] Output length: {len(text)} chars")
    return {
        "_preview": text,
    }


def execute_judge_trace_inspector(inputs: dict, params: dict) -> dict:
    """Observation sink for the Retrieval Judge's verdict list.

    `judge_trace` is the list of {i, keep, reason, source, score} dicts the
    judge emits. We just package it for the node preview — the frontend renders
    the per-chunk keep/drop breakdown. No output; nothing reads from this node.
    """
    trace = inputs.get("judge_trace")
    if not trace:
        return {"_preview": "(no judge trace — connect Retrieval Judge's judge_trace output)"}

    kept = sum(1 for v in trace if v.get("keep"))
    payload = {
        "__judge_trace": True,
        "kept": kept,
        "total": len(trace),
        "verdicts": trace,
    }
    return {"_preview": json.dumps(payload, ensure_ascii=False)}


# ── Eval executors (Editor-only) ───────────────────────────────────

def _parse_facts(raw: str) -> list[str]:
    """Newline-separated facts → cleaned list. Empty input → []."""
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def execute_eval_case_loader(inputs: dict, params: dict) -> dict:
    """Load one case from a golden_set JSON file by case_id."""
    case_id = (params.get("case_id") or "").strip()
    path_str = (params.get("golden_set_path") or "eval/golden_set.json").strip()

    empty_out = {
        "query": "",
        "expected_product": "",
        "expected_facts": "",
        "match_mode": "all",
    }

    if not case_id:
        return {**empty_out, "_preview": "(case_id is empty)"}

    try:
        path = Path(_guard_path(path_str, kind="eval_case_loader golden_set_path"))
    except ValueError as e:
        return {**empty_out, "_preview": f"(path rejected: {e})"}

    if not path.exists():
        return {**empty_out, "_preview": f"(file not found: {path})"}

    try:
        data = json.loads(path.read_text())
    except Exception as e:
        return {**empty_out, "_preview": f"(parse error: {e})"}

    cases = data.get("cases", []) or []
    case = next((c for c in cases if c.get("id") == case_id), None)
    if case is None:
        return {**empty_out, "_preview": f"(case_id not found: {case_id})"}

    facts = case.get("expected_facts") or []
    facts_str = "\n".join(facts)

    preview_obj = {
        "case_id": case_id,
        "category": case.get("category"),
        "expected_language": case.get("expected_language"),
        "expected_product": case.get("expected_product"),
        "expected_facts": facts,
        "match_mode": case.get("match_mode", "all"),
    }
    return {
        "query": case.get("question", ""),
        "expected_product": case.get("expected_product") or "",
        "expected_facts": facts_str,
        "match_mode": case.get("match_mode", "all"),
        "_preview": json.dumps(preview_obj, ensure_ascii=False, indent=2),
    }


def execute_coverage_metric(inputs: dict, params: dict) -> dict:
    """Hit@K + rank of expected_product in top-K retrieved chunks."""
    results = inputs.get("results") or []
    expected = (inputs.get("expected_product") or params.get("expected_product") or "").strip()
    top_k = int(params.get("top_k", 5))

    metric = compute_coverage(results, expected, top_k)
    d = metric["details"]
    if metric["score"] is None:
        preview = f"Coverage: N/A — {d.get('note', '')}"
    else:
        rank_str = f"#{d['rank']}" if d.get("rank") else "miss"
        preview = (
            f"Coverage: {'HIT' if d['hit'] else 'MISS'} ({rank_str} of top-{d['top_k']})\n"
            f"expected: {d['expected_product']}\n"
            f"retrieved: {d['retrieved_products']}"
        )
    return {"metric": metric, "_preview": preview}


def execute_score_distribution_metric(inputs: dict, params: dict) -> dict:
    """Score statistics across top-K."""
    results = inputs.get("results") or []
    top_k = int(params.get("top_k", 5))

    metric = compute_score_distribution(results, top_k)
    d = metric["details"]
    if d.get("count", 0) == 0:
        preview = "Scores: (no results)"
    else:
        preview = (
            f"Scores top-{d['count']}: min={d['min']:.3f} max={d['max']:.3f} "
            f"mean={d['mean']:.3f} std={d['std']:.3f}\n"
            f"top1={d['top1']:.3f} topK={d['topk']:.3f} gap={d['gap_top1_topk']:.3f}"
        )
    return {"metric": metric, "_preview": preview}


def execute_diversity_metric(inputs: dict, params: dict) -> dict:
    """Product diversity / entropy of top-K."""
    results = inputs.get("results") or []
    top_k = int(params.get("top_k", 5))

    metric = compute_diversity(results, top_k)
    d = metric["details"]
    if metric["score"] is None:
        preview = f"Diversity: N/A — {d.get('note', '')}"
    else:
        preview = (
            f"Diversity: {d['unique_products']} products in top-{d['top_k']}, "
            f"entropy_norm={d['entropy_normalized']:.3f}\n"
            f"distribution: {d['distribution']}\n"
            f"dominant: {d['dominant_pid']} ({d['dominant_share']:.0%})"
        )
    return {"metric": metric, "_preview": preview}


def execute_facts_coverage_metric(inputs: dict, params: dict) -> dict:
    """Keyword recall of expected_facts in retrieved text."""
    results = inputs.get("results") or []
    facts_raw = inputs.get("expected_facts")
    if facts_raw is None or facts_raw == "":
        facts_raw = params.get("expected_facts", "")
    facts = _parse_facts(facts_raw)
    match_mode = (inputs.get("match_mode") or params.get("match_mode") or "all").strip()
    if match_mode not in ("all", "any"):
        match_mode = "all"

    metric = compute_facts_coverage(results, facts, match_mode)
    d = metric["details"]
    if metric["score"] is None:
        preview = f"Facts: N/A — {d.get('note', '')}"
    else:
        preview = (
            f"Facts ({d['mode']}): {d['matched_count']}/{d['total_facts']} matched "
            f"(score={metric['score']:.3f})\n"
            f"matched: {d['matched']}\n"
            f"missing: {d['missing']}"
        )
    return {"metric": metric, "_preview": preview}


def execute_eval_report(inputs: dict, params: dict) -> dict:
    """Aggregate up to 4 metrics into a markdown summary. Unwired ports skipped."""
    sections = []
    PASS_THRESHOLD = 0.5  # mirrors eval/scorer.py

    def fmt_score(s):
        if s is None:
            return "N/A"
        flag = "✓" if s >= PASS_THRESHOLD else "✗"
        return f"{s:.3f} {flag}"

    coverage = inputs.get("coverage")
    if isinstance(coverage, dict):
        d = coverage["details"]
        if coverage["score"] is None:
            sections.append(f"### Coverage (Hit@K)\n_{d.get('note', 'N/A')}_\n")
        else:
            rank_str = f"rank #{d['rank']}" if d.get("rank") else "missed"
            sections.append(
                f"### Coverage (Hit@K)\n"
                f"- score: **{fmt_score(coverage['score'])}**\n"
                f"- expected: `{d['expected_product']}`\n"
                f"- result: {'HIT' if d['hit'] else 'MISS'} ({rank_str} in top-{d['top_k']})\n"
                f"- retrieved: `{d['retrieved_products']}`\n"
            )

    scores = inputs.get("score_distribution")
    if isinstance(scores, dict):
        d = scores["details"]
        if d.get("count", 0) == 0:
            sections.append("### Score Distribution\n_no results_\n")
        else:
            sections.append(
                f"### Score Distribution\n"
                f"- top-{d['count']}: min={d['min']:.3f} / max={d['max']:.3f} / mean={d['mean']:.3f} / std={d['std']:.3f}\n"
                f"- top1={d['top1']:.3f}, topK={d['topk']:.3f}, gap={d['gap_top1_topk']:.3f}\n"
                f"- scores: `{d['scores']}`\n"
            )

    diversity = inputs.get("diversity")
    if isinstance(diversity, dict):
        d = diversity["details"]
        if diversity["score"] is None:
            sections.append(f"### Diversity\n_{d.get('note', 'N/A')}_\n")
        else:
            sections.append(
                f"### Diversity\n"
                f"- entropy_normalized: **{fmt_score(diversity['score'])}**\n"
                f"- unique products in top-{d['top_k']}: {d['unique_products']}\n"
                f"- distribution: `{d['distribution']}`\n"
                f"- dominant: `{d['dominant_pid']}` ({d['dominant_share']:.0%})\n"
            )

    facts = inputs.get("facts_coverage")
    if isinstance(facts, dict):
        d = facts["details"]
        if facts["score"] is None:
            sections.append(f"### Facts Coverage\n_{d.get('note', 'N/A')}_\n")
        else:
            sections.append(
                f"### Facts Coverage\n"
                f"- score ({d['mode']}): **{fmt_score(facts['score'])}**\n"
                f"- matched ({d['matched_count']}/{d['total_facts']}): `{d['matched']}`\n"
                f"- missing: `{d['missing']}`\n"
            )

    if not sections:
        report = "# Eval Report\n\n_no metrics wired_"
    else:
        report = "# Eval Report\n\n" + "\n".join(sections)

    return {
        "answer": report,
        "_preview": report,
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
    "price_guard": execute_price_guard,
    "scope_gate": execute_scope_gate,
    "retrieval_judge": execute_retrieval_judge,
    "constraint_filter": execute_constraint_filter,
    "product_selector": execute_product_selector,
    "retriever": execute_retriever,
    "prompt_builder": execute_prompt_builder,
    "system_prompt": execute_system_prompt,
    "generator": execute_generator,
    "output_critic": execute_output_critic,
    "result_display": execute_result_display,
    "judge_trace_inspector": execute_judge_trace_inspector,
    "eval_case_loader": execute_eval_case_loader,
    "coverage_metric": execute_coverage_metric,
    "score_distribution_metric": execute_score_distribution_metric,
    "diversity_metric": execute_diversity_metric,
    "facts_coverage_metric": execute_facts_coverage_metric,
    "eval_report": execute_eval_report,
}
