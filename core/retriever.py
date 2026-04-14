"""
檢索模組。

整合 embedder + vector_store，加上 keyword boosting 提升專有名詞的命中率。
"""

import re
from typing import Dict, List, Optional

import chromadb

from core.embedder import embed_query
from core.vector_store import RetrievalResult, query as vector_query


def retrieve(
    query_text: str,
    collection: chromadb.Collection,
    top_k: int = 5,
    score_threshold: float = 0.3,
    keyword_boost: float = 0.5,
    embedding_model: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
    filters: Optional[dict] = None,
) -> List[RetrievalResult]:
    """語意檢索 + keyword boosting。

    流程：
    1. 將 query 向量化
    2. 從 vector_store 撈出 top_k * 5 筆候選結果
    3. 對包含 query 中專有名詞的結果加分（keyword boosting）
    4. 重新排序，取 top_k 筆
    5. 過濾掉低於 score_threshold 的結果

    Args:
        query_text: 使用者的查詢文字。
        collection: ChromaDB collection。
        top_k: 最終回傳的筆數。
        score_threshold: 最低分數門檻，低於此值的結果會被過濾。
        keyword_boost: 每命中一個專有名詞的加分值。
        embedding_model: Ollama embedding 模型名稱。
        base_url: Ollama API base URL。
        filters: ChromaDB metadata 過濾條件。

    Returns:
        List[RetrievalResult]: 按相關度排序的檢索結果。
    """
    # 1. Query 向量化
    query_embedding = embed_query(query_text, model=embedding_model, base_url=base_url)

    # 2. 擴大搜尋範圍，撈出更多候選結果供 boosting 重排
    expanded_k = min(max(top_k * 5, 10), collection.count())
    if expanded_k < 1:
        expanded_k = 1

    candidates = vector_query(
        collection=collection,
        query_embedding=query_embedding,
        top_k=expanded_k,
        filters=filters,
    )

    if not candidates:
        print("[Retriever] No candidates found.")
        return []

    # 3. Keyword Boosting — 抓出 query 中的專有名詞（英數字 >= 3 字元）
    keywords = re.findall(r"[a-zA-Z0-9_]{3,}", query_text)

    if keywords:
        for result in candidates:
            content_lower = result.chunk.text.lower()
            bonus = sum(
                keyword_boost for kw in keywords if kw.lower() in content_lower
            )
            result.score += bonus

    # 4. 重新排序，取 top_k
    candidates.sort(key=lambda r: r.score, reverse=True)
    top_results = candidates[:top_k]

    # 5. Debug 輸出分數
    print(f"[Retriever] Scores for query: '{query_text[:50]}...'")
    for r in top_results:
        source = r.chunk.metadata.get("filename", "unknown")
        print(f"  [{source}] Score: {r.score:.3f}")

    # 6. Threshold 過濾
    filtered = [r for r in top_results if r.score >= score_threshold]

    if not filtered and top_results:
        best = top_results[0].score
        print(f"[Retriever] All results below threshold {score_threshold} (best: {best:.3f})")

    print(f"[Retriever] Returning {len(filtered)} results (threshold={score_threshold})")
    return filtered
