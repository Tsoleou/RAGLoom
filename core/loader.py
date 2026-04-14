"""
文件載入模組。

支援 .txt, .md, .csv, .pdf 格式，將檔案內容讀取為統一的 Document 資料結構。
"""

import csv
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class Document:
    """代表一份載入的文件。"""
    content: str
    metadata: dict = field(default_factory=dict)


# 支援的副檔名
SUPPORTED_EXTENSIONS = {".txt", ".md", ".csv", ".pdf"}


def load_file(file_path: str) -> Document:
    """載入單一檔案，回傳 Document。

    Args:
        file_path: 檔案的絕對或相對路徑。

    Returns:
        Document: 包含檔案內容與 metadata。

    Raises:
        FileNotFoundError: 檔案不存在。
        ValueError: 不支援的檔案格式。
    """
    path = Path(file_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"檔案不存在：{path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支援的檔案格式：{ext}（支援 {', '.join(SUPPORTED_EXTENSIONS)}）")

    metadata = {
        "source": str(path),
        "filename": path.name,
        "type": ext.lstrip("."),
    }

    # Derive product_id from filename convention: "product_<id>.<ext>" → <id>.
    # Used by metadata-filtered retrieval so the LLM can pull all chunks for
    # a specific product regardless of semantic similarity to the query.
    stem = path.stem  # filename without extension
    if stem.startswith("product_"):
        metadata["product_id"] = stem[len("product_"):]

    if ext in (".txt", ".md"):
        content = _load_text(path)
    elif ext == ".csv":
        content = _load_csv(path)
    elif ext == ".pdf":
        content = _load_pdf(path)

    print(f"[Loader] Loaded {path.name} ({len(content)} chars)")
    return Document(content=content, metadata=metadata)


def load_reference_text(path: str) -> str:
    """Load a file or directory as always-on reference material (no chunking).

    Used for small product-comparison tables, pricing sheets, etc. that should
    be injected verbatim into the prompt instead of going through RAG retrieval.
    If path is a directory, all supported files inside are concatenated in
    filename order, each preceded by a ``# filename`` header.

    Args:
        path: A file path or directory path.

    Returns:
        str: Concatenated reference text (empty string if path doesn't exist
             or contains no supported files).
    """
    p = Path(path).resolve()
    if not p.exists():
        print(f"[Loader] Reference path does not exist: {p}")
        return ""

    files: list[Path]
    if p.is_file():
        files = [p]
    else:
        files = sorted(
            f for f in p.iterdir()
            if f.is_file()
            and not f.name.startswith(".")
            and f.suffix.lower() in SUPPORTED_EXTENSIONS
        )

    if not files:
        print(f"[Loader] No reference files found in: {p}")
        return ""

    blocks: list[str] = []
    for f in files:
        try:
            if f.suffix.lower() in (".txt", ".md"):
                text = _load_text(f)
            elif f.suffix.lower() == ".csv":
                text = _load_csv(f)
            elif f.suffix.lower() == ".pdf":
                text = _load_pdf(f)
            else:
                continue
            blocks.append(f"# {f.name}\n{text.strip()}")
            print(f"[Loader] Reference loaded: {f.name} ({len(text)} chars)")
        except Exception as e:
            print(f"[Loader] Error reading reference {f.name}: {e}")

    return "\n\n".join(blocks)


def load_directory(dir_path: str) -> List[Document]:
    """載入資料夾內所有支援格式的檔案。

    Args:
        dir_path: 資料夾路徑。

    Returns:
        List[Document]: 所有成功載入的文件清單。

    Raises:
        FileNotFoundError: 資料夾不存在。
    """
    path = Path(dir_path).resolve()

    if not path.exists():
        raise FileNotFoundError(f"資料夾不存在：{path}")
    if not path.is_dir():
        raise ValueError(f"路徑不是資料夾：{path}")

    docs = []
    files = sorted(path.iterdir())
    print(f"[Loader] Scanning {path} — found {len(files)} items")

    for f in files:
        # 跳過隱藏檔和資料夾
        if f.name.startswith(".") or not f.is_file():
            continue

        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print(f"[Loader] Skipped unsupported file: {f.name}")
            continue

        try:
            doc = load_file(str(f))
            docs.append(doc)
        except Exception as e:
            print(f"[Loader] Error reading {f.name}: {e}")

    print(f"[Loader] Total documents loaded: {len(docs)}")
    return docs


# --- 內部讀取函式 ---

def _load_text(path: Path) -> str:
    """讀取純文字檔（.txt, .md）。"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_csv(path: Path) -> str:
    """讀取 CSV，保留原始格式。格式轉換交給 chunker 處理。"""
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_pdf(path: Path) -> str:
    """讀取 PDF，擷取所有頁面的文字。"""
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ImportError("讀取 PDF 需要 pymupdf，請執行：pip install pymupdf")

    doc = fitz.open(str(path))
    pages = []
    for page in doc:
        text = page.get_text()
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n".join(pages)
