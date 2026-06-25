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


def _csv(s: str) -> list[str]:
    return [x.strip() for x in s.split(",") if x.strip()]


def _bool(s: str) -> bool:
    return s.strip().lower() in ("1", "true", "yes", "on")


def _env(key: str, cast, default):
    """讀環境變數並轉型；未設或轉型失敗 → 退回 default（可為 callable）。

    讓 dataclass 的 default_factory 與 from_env 共用同一套邏輯，使得
    Settings() 與 Settings.from_env() 都尊重 env 覆蓋。這很重要：executors
    與 chat 路徑很多地方直接 Settings()（非 from_env），容器部署時必須靠這裡
    才能讓 RAG_OLLAMA_BASE_URL 等 env 生效（否則永遠連 localhost）。
    """
    raw = os.environ.get(key)
    if raw is None:
        return default() if callable(default) else default
    try:
        return cast(raw)
    except (ValueError, TypeError):
        print(f"[Settings] 警告：環境變數 {key}={raw} 轉型失敗，使用預設值")
        return default() if callable(default) else default


@dataclass
class Settings:
    """RAG Pipeline 設定。所有參數皆可透過環境變數覆蓋（Settings() 即生效）。"""

    # Ollama API
    ollama_base_url: str = field(default_factory=lambda: _env("RAG_OLLAMA_BASE_URL", str, "http://localhost:11434"))
    llm_model: str = field(default_factory=lambda: _env("RAG_LLM_MODEL", str, "gemma3:4b"))
    embedding_model: str = field(default_factory=lambda: _env("RAG_EMBEDDING_MODEL", str, "nomic-embed-text"))

    # ChromaDB
    chroma_persist_path: str = field(default_factory=lambda: _env("RAG_CHROMA_PERSIST_PATH", str, "./chroma_db"))

    # 檢索參數
    top_k: int = field(default_factory=lambda: _env("RAG_TOP_K", int, 5))
    score_threshold: float = field(default_factory=lambda: _env("RAG_SCORE_THRESHOLD", float, 0.3))
    keyword_boost: float = field(default_factory=lambda: _env("RAG_KEYWORD_BOOST", float, 0.3))

    # 切割參數
    chunk_size: int = field(default_factory=lambda: _env("RAG_CHUNK_SIZE", int, 500))
    chunk_overlap: int = field(default_factory=lambda: _env("RAG_CHUNK_OVERLAP", int, 50))

    # 輸出模式：professional | chatbot
    output_mode: str = field(default_factory=lambda: _env("RAG_OUTPUT_MODE", str, "professional"))

    # Query 數值約束過濾（Exp2a）：LLM 抽約束 + code 比較過濾。
    # 關掉 = 維持純檢索行為（eval A/B baseline）。
    constraint_filter_enabled: bool = field(default_factory=lambda: _env("RAG_CONSTRAINT_FILTER", _bool, True))

    # ── API 安全 ────────────────────────────────────────────────
    # 空字串 = server 啟動時自動生成，並寫進 .env.local 給前端 vite 讀
    api_local_token: str = field(default_factory=lambda: _env("RAG_API_TOKEN", str, ""))
    # CORS allowed origins（逗號分隔；預設只接受本機 vite dev server）
    api_allowed_origins: list[str] = field(
        default_factory=lambda: _env(
            "RAG_API_ALLOWED_ORIGINS", _csv,
            lambda: ["http://localhost:5173", "http://127.0.0.1:5173"],
        )
    )
    # Path guard 允許的根目錄（逗號分隔；graph 上的 source_path/persist_path
    # 等欄位必須落在這些目錄之下）
    allowed_data_roots: list[str] = field(
        default_factory=lambda: _env(
            "RAG_ALLOWED_DATA_ROOTS", _csv,
            lambda: ["./knowledge_base", "./eval", "./chroma_db"],
        )
    )

    @classmethod
    def from_env(cls, env_path: str = ".env") -> "Settings":
        """載入 .env 檔到 os.environ，再建立 Settings。

        實際的 env→欄位轉型由各欄位的 default_factory（共用 _env）完成，所以
        from_env 與直接 Settings() 行為一致；from_env 額外負責先把 .env 檔讀進來。
        """
        _load_env_file(env_path)
        return cls()

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
