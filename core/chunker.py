"""
文本切割模組。

將 Document 切割成多個 Chunk，支援多種切割策略。
- fixed：固定字數，優先在自然斷點切割。
- section：按標題結構切割，適合規格表、文件等有章節的內容。
- csv_row：CSV 每列轉換為換行 key-value 格式，每列一個 chunk。
"""

import csv
import io
import re
from dataclasses import dataclass, field
from typing import List, Tuple

from core.loader import Document


@dataclass
class Chunk:
    """代表一個切割後的文本片段。"""
    text: str
    metadata: dict = field(default_factory=dict)


def chunk_document(
    doc: Document,
    strategy: str = "fixed",
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> List[Chunk]:
    """將 Document 切割成多個 Chunk。

    Args:
        doc: 要切割的文件。
        strategy: 切割策略 — "fixed"（固定字數）。
        chunk_size: 每個 chunk 的最大字數。
        chunk_overlap: 相鄰 chunk 之間的重疊字數。

    Returns:
        List[Chunk]: 切割後的 chunk 清單。
    """
    if strategy == "fixed":
        return _chunk_fixed(doc, chunk_size, chunk_overlap)
    elif strategy == "section":
        return _chunk_section(doc, chunk_size, chunk_overlap)
    elif strategy == "csv_row":
        return _chunk_csv_row(doc)
    else:
        print(f"[Chunker] 未知策略 '{strategy}'，fallback 到 fixed")
        return _chunk_fixed(doc, chunk_size, chunk_overlap)


# 自然斷點，按優先順序嘗試：段落 > 換行 > 空格
_SEPARATORS = ["\n\n", "\n", " "]


def _find_break_point(text: str, chunk_size: int) -> int:
    """在 chunk_size 範圍內找最近的自然斷點。

    從 chunk_size 位置往回找，依序嘗試 \\n\\n、\\n、空格。
    如果都找不到（例如一整段沒有任何空白），就硬切在 chunk_size。
    """
    if len(text) <= chunk_size:
        return len(text)

    for sep in _SEPARATORS:
        # 在 chunk_size 範圍內，從後往前找最近的斷點
        pos = text.rfind(sep, 0, chunk_size)
        # 至少要保留一半的 chunk_size，避免切出太短的段落
        if pos > chunk_size // 2:
            return pos + len(sep)

    # 找不到任何自然斷點，硬切
    return chunk_size


def _chunk_fixed(doc: Document, chunk_size: int, chunk_overlap: int) -> List[Chunk]:
    """固定字數切割，優先在自然斷點（段落、換行、空格）處斷開。

    邏輯：從頭開始，每次在 chunk_size 範圍內找最近的自然斷點切割，
    下一段從 (切割點 - chunk_overlap) 開始，確保段落之間有重疊。
    """
    text = doc.content
    if not text.strip():
        return []

    chunks = []
    start = 0

    while start < len(text):
        remaining = text[start:]
        break_at = _find_break_point(remaining, chunk_size)
        chunk_text = remaining[:break_at].strip()

        if chunk_text:
            chunks.append(Chunk(
                text=chunk_text,
                metadata={
                    **doc.metadata,
                    "chunk_index": len(chunks),
                },
            ))

        # 移動起始位置，保留 overlap
        step = max(break_at - chunk_overlap, 1)
        start += step

    print(f"[Chunker] '{doc.metadata.get('filename', '?')}' → {len(chunks)} chunks (size={chunk_size}, overlap={chunk_overlap})")
    return chunks


# 標題偵測模式：數字編號、Markdown 標題、方括號標題
_SECTION_PATTERN = re.compile(
    r"^(?:"
    r"\d+\.\s+"          # "1. Core Specs"
    r"|#{1,6}\s+"        # "## 標題"
    r"|\[.+\]"           # "[PRODUCT SHEET: ...]"
    r")",
    re.MULTILINE,
)


def _split_sections(text: str) -> List[Tuple[str, str]]:
    """將文本按標題拆成 (title, body) 清單。

    如果找不到任何標題，回傳空清單（讓呼叫端 fallback）。
    標題行之前若有無標題的前言文字，以空字串 title 保留。
    """
    matches = list(_SECTION_PATTERN.finditer(text))
    if not matches:
        return []

    sections: List[Tuple[str, str]] = []

    # 標題前的前言
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            sections.append(("", preamble))

    for i, m in enumerate(matches):
        # 本 section 的範圍：從這個標題到下一個標題（或文件結尾）
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        block = text[m.start() : end].strip()
        if not block:
            continue

        # 標題 = 第一行，body = 剩餘
        first_newline = block.find("\n")
        if first_newline == -1:
            title = block
        else:
            title = block[:first_newline].strip()

        sections.append((title, block))

    return sections


def _chunk_section(
    doc: Document, chunk_size: int, chunk_overlap: int
) -> List[Chunk]:
    """按標題結構切割文件。

    每個 section 成為一個 chunk。若單一 section 超過 chunk_size，
    則對該 section 進行 fixed 子切割。找不到標題時 fallback 到 fixed。
    """
    sections = _split_sections(doc.content)

    if not sections:
        print(f"[Chunker] '{doc.metadata.get('filename', '?')}' 無標題結構，fallback 到 fixed")
        return _chunk_fixed(doc, chunk_size, chunk_overlap)

    chunks: List[Chunk] = []

    for title, body in sections:
        if len(body) <= chunk_size:
            chunks.append(Chunk(
                text=body,
                metadata={
                    **doc.metadata,
                    "chunk_index": len(chunks),
                    "section_title": title,
                },
            ))
        else:
            # section 太長，用 fixed 再切
            sub_doc = Document(content=body, metadata=doc.metadata)
            sub_chunks = _chunk_fixed(sub_doc, chunk_size, chunk_overlap)
            for sc in sub_chunks:
                sc.metadata["chunk_index"] = len(chunks)
                sc.metadata["section_title"] = title
                chunks.append(sc)

    print(f"[Chunker] '{doc.metadata.get('filename', '?')}' → {len(chunks)} chunks (strategy=section)")
    return chunks


def _chunk_csv_row(doc: Document) -> List[Chunk]:
    """CSV 每列轉為換行 key-value 格式，每列一個 chunk。

    範例輸出：
        系列: X1
        型號: StarForge X1
        CPU: Intel Core Ultra 9 185H
        ...
    """
    reader = csv.reader(io.StringIO(doc.content))
    rows = list(reader)

    if len(rows) < 2:
        print(f"[Chunker] '{doc.metadata.get('filename', '?')}' CSV 不足兩列，跳過")
        return []

    headers = rows[0]
    chunks: List[Chunk] = []

    for i, row in enumerate(rows[1:]):
        if not any(cell.strip() for cell in row):
            continue  # 跳過空列

        lines = []
        for header, value in zip(headers, row):
            value = value.strip()
            if value:
                lines.append(f"{header}: {value}")

        if not lines:
            continue

        chunk_text = "\n".join(lines)

        # 用第二欄（通常是型號名稱）作為識別標籤
        label = row[1].strip() if len(row) > 1 and row[1].strip() else f"row_{i}"

        chunks.append(Chunk(
            text=chunk_text,
            metadata={
                **doc.metadata,
                "chunk_index": len(chunks),
                "row_index": i,
                "row_label": label,
            },
        ))

    print(f"[Chunker] '{doc.metadata.get('filename', '?')}' → {len(chunks)} chunks (strategy=csv_row)")
    return chunks
