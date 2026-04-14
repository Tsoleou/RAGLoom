"""
RAG Pipeline 全域設定模組。

使用 dataclass 定義所有可調整參數，支援從 .env 檔案讀取覆蓋值。
"""

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_env_file(env_path: str = ".env") -> None:
    """從 .env 檔案載入環境變數（不依賴 python-dotenv）。"""
    env_file = Path(env_path)
    if not env_file.exists():
        return
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass
class Settings:
    """RAG Pipeline 設定。所有參數皆可透過環境變數覆蓋。"""

    # Ollama API
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "gemma3:4b"
    embedding_model: str = "nomic-embed-text"

    # ChromaDB
    chroma_persist_path: str = "./chroma_db"

    # 檢索參數
    top_k: int = 5
    score_threshold: float = 0.3
    keyword_boost: float = 0.5

    # 切割參數
    chunk_size: int = 500
    chunk_overlap: int = 50

    # 輸出模式：professional | chatbot
    output_mode: str = "professional"

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "Settings":
        """從環境變數建立 Settings，.env 檔案中的值會覆蓋預設值。"""
        _load_env_file(env_path)

        env_map = {
            "ollama_base_url": ("RAG_OLLAMA_BASE_URL", str),
            "llm_model": ("RAG_LLM_MODEL", str),
            "embedding_model": ("RAG_EMBEDDING_MODEL", str),
            "chroma_persist_path": ("RAG_CHROMA_PERSIST_PATH", str),
            "top_k": ("RAG_TOP_K", int),
            "score_threshold": ("RAG_SCORE_THRESHOLD", float),
            "keyword_boost": ("RAG_KEYWORD_BOOST", float),
            "chunk_size": ("RAG_CHUNK_SIZE", int),
            "chunk_overlap": ("RAG_CHUNK_OVERLAP", int),
            "output_mode": ("RAG_OUTPUT_MODE", str),
        }

        kwargs = {}
        for field_name, (env_key, cast_fn) in env_map.items():
            env_val = os.environ.get(env_key)
            if env_val is not None:
                try:
                    kwargs[field_name] = cast_fn(env_val)
                except (ValueError, TypeError):
                    print(f"[Settings] 警告：環境變數 {env_key}={env_val} 轉型失敗，使用預設值")

        return cls(**kwargs)

    def __post_init__(self) -> None:
        """驗證參數合理性。"""
        if self.top_k < 1:
            raise ValueError(f"top_k 必須 >= 1，收到 {self.top_k}")
        if self.chunk_size < 100:
            raise ValueError(f"chunk_size 必須 >= 100，收到 {self.chunk_size}")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(f"chunk_overlap ({self.chunk_overlap}) 必須小於 chunk_size ({self.chunk_size})")
        if self.output_mode not in ("professional", "chatbot"):
            raise ValueError(f"output_mode 必須是 'professional' 或 'chatbot'，收到 '{self.output_mode}'")
