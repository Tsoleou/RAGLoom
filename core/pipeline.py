"""
RAG Pipeline 整合模組。

串接 loader → chunker → embedder → vector_store → retriever → prompt_builder → generator，
提供 ingest（建立知識庫）和 query（問答）兩個主要操作。
"""

import shutil
from typing import Optional

from config.settings import Settings
from core.loader import load_directory, load_file, load_reference_text
from core.chunker import chunk_document
from core.embedder import embed_chunks
from core.vector_store import (
    get_client,
    create_collection,
    add_chunks,
    delete_collection,
)
from core.retriever import retrieve
from core.prompt_builder import build_prompt
from core.generator import generate, GenerationResult
from core.personas import get_preset, PROFESSIONAL


class RAGPipeline:
    """RAG 問答管線，整合所有核心模組。"""

    def __init__(self, config: Optional[Settings] = None):
        """初始化管線。

        Args:
            config: 設定物件。若未提供，使用預設值。
        """
        self.config = config or Settings()
        self.client = get_client(self.config.chroma_persist_path)
        self.collection = create_collection(self.client)
        self._context = []  # Ollama 多輪對話 context tokens
        self._last_retrieval = []  # 最近一次檢索結果
        # Always-on reference data (product comparison tables, etc.)
        self._reference_data = load_reference_text("./knowledge_base/_reference")

        print(f"[Pipeline] Initialized (LLM={self.config.llm_model}, Embedding={self.config.embedding_model})")

    def ingest(self, source_path: str) -> int:
        """載入知識庫：load → chunk → embed → store。

        Args:
            source_path: 檔案或資料夾路徑。

        Returns:
            int: 成功寫入的 chunk 數量。
        """
        # 1. Load
        if source_path.endswith(('.txt', '.md', '.csv', '.pdf')):
            docs = [load_file(source_path)]
        else:
            docs = load_directory(source_path)

        if not docs:
            print("[Pipeline] No documents found, nothing to ingest.")
            return 0

        # 2. Chunk（依檔案類型自動選策略）
        all_chunks = []
        for doc in docs:
            file_type = doc.metadata.get("type", "")
            if file_type == "csv":
                strategy = "csv_row"
            else:
                strategy = "section"

            chunks = chunk_document(
                doc,
                strategy=strategy,
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
            )
            all_chunks.extend(chunks)

        if not all_chunks:
            print("[Pipeline] No chunks created, nothing to ingest.")
            return 0

        # 3. Embed
        embeddings = embed_chunks(
            all_chunks,
            model=self.config.embedding_model,
            base_url=self.config.ollama_base_url,
        )

        # 4. Store
        add_chunks(self.collection, all_chunks, embeddings)

        print(f"[Pipeline] Ingested {len(all_chunks)} chunks from {len(docs)} documents")
        return len(all_chunks)

    def query(
        self,
        question: str,
        mode: Optional[str] = None,
        glossary: str = "",
        vision_context: str = "",
    ) -> GenerationResult:
        """問答：retrieve → build_prompt → generate。

        Args:
            question: 使用者的問題。
            mode: 輸出模式（"professional" 或 "chatbot"）。若未指定，使用設定值。
            glossary: 產品詞典文字。
            vision_context: 圖片分析結果（選用）。

        Returns:
            GenerationResult: LLM 生成的回答。
        """
        mode = mode or self.config.output_mode

        # 1. Retrieve
        results = retrieve(
            query_text=question,
            collection=self.collection,
            top_k=self.config.top_k,
            score_threshold=self.config.score_threshold,
            keyword_boost=self.config.keyword_boost,
            embedding_model=self.config.embedding_model,
            base_url=self.config.ollama_base_url,
        )

        self._last_retrieval = results

        # 2. Build context-only prompt
        prompt = build_prompt(
            query=question,
            contexts=results,
            glossary=glossary,
            reference_data=self._reference_data,
            vision_context=vision_context,
        )

        # 3. Resolve persona from preset and prepend it to the system text
        persona = get_preset(mode) or PROFESSIONAL
        prompt = {
            **prompt,
            "system": f"{persona.text}\n\n{prompt['system']}",
        }

        # 4. Generate
        generation = generate(
            prompt=prompt,
            model=self.config.llm_model,
            format_type=persona.format_hint,
            context=self._context,
            base_url=self.config.ollama_base_url,
        )

        # 更新多輪對話 context
        self._context = generation.context

        return generation

    def reset_collection(self) -> None:
        """清空並重建知識庫 collection。"""
        name = self.collection.name
        delete_collection(self.client, name)
        self.collection = create_collection(self.client)
        self._context = []
        self._last_retrieval = []
        print(f"[Pipeline] Collection '{name}' reset")

    def reset_conversation(self) -> None:
        """清除多輪對話 context（不影響知識庫）。"""
        self._context = []
        self._last_retrieval = []
        print("[Pipeline] Conversation context cleared")
