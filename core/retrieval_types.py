"""
檢索結果型別。

RetrievalResult 從 vector_store 抽出來放這裡，因為它只依賴 Chunk、不碰
chromadb。constraint_filter / scope_gate / eval_metrics / prompt_builder /
retrieval_judge 等純邏輯模組（以及它們的單元測試）可以 import 這個型別，而
不被迫把整包 chromadb 拉進 import chain。

vector_store 仍 re-export 這個名稱，所以既有的
`from core.vector_store import RetrievalResult` 不會壞。
"""

from dataclasses import dataclass

from core.chunker import Chunk


@dataclass
class RetrievalResult:
    """代表一筆檢索結果。"""
    chunk: Chunk
    score: float       # 相似度分數（越高越相關）
    distance: float    # 向量距離（越低越近）
