"""
Embedding 向量化模組。

透過 Ollama API 將文字轉為向量，用於後續的語意檢索。
"""

import requests
from typing import List

from core.chunker import Chunk


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

    for i, chunk in enumerate(chunks):
        vector = _call_embedding_api(url, model, chunk.text)
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
    vector = _call_embedding_api(url, model, query)
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
