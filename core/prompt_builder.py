"""
Prompt 組裝模組。

純粹做 context assembly：把 RAG 檢索結果（+ 選用的詞典 / vision context）拼成
一個 system 文字區塊，搭配 user 問題回傳。

Persona / output format 已不再屬於 PromptBuilder 的職責 — 那些屬於 SystemPrompt
node 跟 Generator 的 format_type，這邊只負責「context 拼裝」。
"""

from typing import List

from core.vector_store import RetrievalResult


def build_prompt(
    query: str,
    contexts: List[RetrievalResult],
    glossary: str = "",
    reference_data: str = "",
    vision_context: str = "",
) -> dict:
    """組裝 RAG context 區塊。

    Args:
        query: 使用者的問題。
        contexts: 檢索結果清單。
        glossary: 產品詞典文字（選用，會接在 context 區塊前）。
        reference_data: Always-on reference material such as product comparison
            tables — injected verbatim so the LLM always has breadth coverage,
            even when RAG retrieval misses some entities.
        vision_context: 圖片分析結果（選用）。

    Returns:
        dict: {"system": context_block, "user": query}
              其中 system 只包含 RAG / glossary / reference / vision — 不含 persona。
    """
    parts: list[str] = []

    if glossary.strip():
        parts.append(glossary.strip())

    if reference_data.strip():
        parts.append(f"[Product Reference]:\n{reference_data.strip()}")

    if contexts:
        parts.append(f"[Internal Knowledge]:\n{_format_contexts(contexts)}")
    else:
        parts.append("[Internal Knowledge]:\nNo relevant knowledge found.")

    if vision_context.strip():
        parts.append(f"[Visual Analysis]:\n{vision_context.strip()}")

    print(
        f"[PromptBuilder] Contexts: {len(contexts)} | "
        f"Glossary: {'Yes' if glossary.strip() else 'No'} | "
        f"Reference: {'Yes' if reference_data.strip() else 'No'} | "
        f"Vision: {'Yes' if vision_context.strip() else 'No'}"
    )

    return {
        "system": "\n\n".join(parts),
        "user": query,
    }


def _format_contexts(contexts: List[RetrievalResult]) -> str:
    """將檢索結果格式化為文字區塊。"""
    blocks = []
    for r in contexts:
        source = r.chunk.metadata.get("filename", "unknown")
        blocks.append(f"[Source: {source}] (Score: {r.score:.3f})\n{r.chunk.text}")
    return "\n\n".join(blocks)
