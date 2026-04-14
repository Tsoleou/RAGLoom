"""
向量資料庫模組。

使用 ChromaDB 本地持久化模式，提供 collection 建立、向量寫入、語意查詢功能。
"""

import chromadb
from dataclasses import dataclass
from typing import List, Optional

from core.chunker import Chunk


@dataclass
class RetrievalResult:
    """代表一筆檢索結果。"""
    chunk: Chunk
    score: float       # 相似度分數（越高越相關）
    distance: float    # 向量距離（越低越近）


def get_client(persist_path: str = "./chroma_db") -> chromadb.ClientAPI:
    """取得 ChromaDB 持久化 client。

    Args:
        persist_path: 資料庫儲存路徑。

    Returns:
        chromadb.ClientAPI: ChromaDB client 實例。
    """
    client = chromadb.PersistentClient(path=persist_path)
    print(f"[VectorStore] Connected to ChromaDB at: {persist_path}")
    return client


def create_collection(
    client: chromadb.ClientAPI,
    name: str = "rag_collection",
) -> chromadb.Collection:
    """建立或取得 collection（若已存在則直接取得）。

    Args:
        client: ChromaDB client。
        name: Collection 名稱。

    Returns:
        chromadb.Collection: collection 實例。
    """
    collection = client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},  # 使用 cosine similarity
    )
    print(f"[VectorStore] Collection '{name}' ready (existing docs: {collection.count()})")
    return collection


def add_chunks(
    collection: chromadb.Collection,
    chunks: List[Chunk],
    embeddings: List[List[float]],
) -> None:
    """將 chunks 和對應的向量寫入 collection。

    Args:
        collection: 目標 collection。
        chunks: 要寫入的 chunk 清單。
        embeddings: 每個 chunk 對應的向量（順序必須一致）。

    Raises:
        ValueError: chunks 和 embeddings 數量不一致。
    """
    if len(chunks) != len(embeddings):
        raise ValueError(
            f"chunks ({len(chunks)}) 和 embeddings ({len(embeddings)}) 數量不一致"
        )

    if not chunks:
        print("[VectorStore] No chunks to add, skipping.")
        return

    # 準備寫入資料
    ids = [f"{chunk.metadata.get('filename', 'doc')}_{chunk.metadata.get('chunk_index', i)}"
           for i, chunk in enumerate(chunks)]
    documents = [chunk.text for chunk in chunks]
    metadatas = [chunk.metadata for chunk in chunks]

    collection.add(
        ids=ids,
        documents=documents,
        embeddings=embeddings,
        metadatas=metadatas,
    )
    print(f"[VectorStore] Added {len(chunks)} chunks to '{collection.name}' (total: {collection.count()})")


def query(
    collection: chromadb.Collection,
    query_embedding: List[float],
    top_k: int = 5,
    filters: Optional[dict] = None,
) -> List[RetrievalResult]:
    """用向量查詢最相關的 chunks。

    Args:
        collection: 要查詢的 collection。
        query_embedding: 查詢文字的向量。
        top_k: 回傳最相關的前幾筆。
        filters: ChromaDB metadata 過濾條件（例如 {"type": "csv"}）。

    Returns:
        List[RetrievalResult]: 按相關度排序的檢索結果。
    """
    query_params = {
        "query_embeddings": [query_embedding],
        "n_results": min(top_k, collection.count()) or 1,
        "include": ["documents", "metadatas", "distances"],
    }
    if filters:
        query_params["where"] = filters

    results = collection.query(**query_params)

    # 解析結果
    retrieval_results = []
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]
    distances = results.get("distances", [[]])[0]

    for doc_text, metadata, distance in zip(documents, metadatas, distances):
        # cosine distance → similarity score: score = 1 - distance
        score = 1.0 - distance

        chunk = Chunk(text=doc_text, metadata=metadata)
        retrieval_results.append(RetrievalResult(
            chunk=chunk,
            score=score,
            distance=distance,
        ))

    print(f"[VectorStore] Query returned {len(retrieval_results)} results from '{collection.name}'")
    return retrieval_results


def delete_collection(client: chromadb.ClientAPI, name: str) -> None:
    """刪除指定的 collection。

    Args:
        client: ChromaDB client。
        name: 要刪除的 collection 名稱。
    """
    client.delete_collection(name)
    print(f"[VectorStore] Deleted collection '{name}'")
