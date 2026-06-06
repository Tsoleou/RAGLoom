"""
Embedding 向量化模組。

透過 Ollama API 將文字轉為向量，用於後續的語意檢索。
"""

import requests
from typing import List, Tuple

from core.chunker import Chunk


# Per-model retrieval prompts. Ollama's /api/embeddings sends the input
# verbatim (TEMPLATE is bare `{{ .Prompt }}` for these models — verified), so
# the documented doc/query prefixes are NOT applied automatically. Both
# EmbeddingGemma and Qwen3-Embedding are trained with an asymmetric prompt
# (document side vs query side); skipping it measurably degrades retrieval
# (cos(raw, prefixed) ≈ 0.89 / 0.93). nomic-embed-text is left raw on purpose
# so the baseline stays comparable to prior eval history.
#   key: model name prefix → (doc_prefix, query_prefix)
_RETRIEVAL_PREFIXES: dict[str, Tuple[str, str]] = {
    # Google EmbeddingGemma — https://ai.google.dev/gemma/docs/embeddinggemma
    "embeddinggemma": (
        "title: none | text: ",
        "task: search result | query: ",
    ),
    # Qwen3-Embedding — doc side raw, query side carries an Instruct preamble.
    "qwen3-embedding": (
        "",
        "Instruct: Given a search query, retrieve relevant passages that answer the query\nQuery: ",
    ),
}


def _prefixes_for(model: str) -> Tuple[str, str]:
    """Return (doc_prefix, query_prefix) for an embedding model.

    Matched by name prefix (case-insensitive) so size-tagged variants
    (e.g. qwen3-embedding:0.6b) and capitalised ids (e.g. EmbeddingGemma)
    resolve correctly. Unknown models get no prefix (raw text).
    """
    model_lc = model.lower()
    for name, prefixes in _RETRIEVAL_PREFIXES.items():
        if model_lc.startswith(name):
            return prefixes
    return "", ""


def embed_chunks(
    chunks: List[Chunk],
    model: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
) -> List[List[float]]:
    """將多個 Chunk 批次轉為向量。

    Args:
        chunks: 要向量化的 chunk 清單。
        model: Ollama embedding 模型名稱。
        base_url: Ollama API 的 base URL。

    Returns:
        List[List[float]]: 每個 chunk 對應的向量。
    """
    embeddings = []
    url = f"{base_url}/api/embeddings"
    doc_prefix, _ = _prefixes_for(model)

    for i, chunk in enumerate(chunks):
        vector = _call_embedding_api(url, model, doc_prefix + chunk.text)
        embeddings.append(vector)

    print(f"[Embedder] Embedded {len(embeddings)} chunks (model={model}, dim={len(embeddings[0]) if embeddings else 0})")
    return embeddings


def embed_query(
    query: str,
    model: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
) -> List[float]:
    """將單一查詢文字轉為向量。

    Args:
        query: 使用者的查詢文字。
        model: Ollama embedding 模型名稱。
        base_url: Ollama API 的 base URL。

    Returns:
        List[float]: 查詢文字的向量。
    """
    url = f"{base_url}/api/embeddings"
    _, query_prefix = _prefixes_for(model)
    vector = _call_embedding_api(url, model, query_prefix + query)
    print(f"[Embedder] Embedded query ({len(vector)} dims)")
    return vector


def _call_embedding_api(url: str, model: str, text: str) -> List[float]:
    """呼叫 Ollama embedding API。

    Args:
        url: API endpoint。
        model: 模型名稱。
        text: 要向量化的文字。

    Returns:
        List[float]: 向量。

    Raises:
        ConnectionError: 無法連線到 Ollama。
        RuntimeError: API 回傳錯誤。
    """
    if not text or not text.strip():
        raise RuntimeError("Embedding 收到空字串，跳過此 chunk")

    payload = {"model": model, "prompt": text}

    try:
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
    except requests.ConnectionError:
        raise ConnectionError(
            f"無法連線到 Ollama ({url})。請確認 Ollama 正在運行。"
        )
    except requests.HTTPError as e:
        raise RuntimeError(f"Ollama API 錯誤：{e}")

    data = response.json()
    embedding = data.get("embedding")
    if not embedding:
        raise RuntimeError(f"Ollama 回傳格式異常，缺少 embedding 欄位：{data}")

    return embedding
