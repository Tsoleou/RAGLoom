"""
LLM 生成模組。

呼叫 Ollama /api/chat 生成回答，支援 text / json 輸出格式與多輪對話 messages。
"""

import re
import requests
from dataclasses import dataclass, field
from typing import List, Optional, Union


def _detect_language(text: str) -> str:
    """Return 'Chinese' if the text contains any CJK character, else 'English'."""
    return "Chinese" if re.search(r"[一-鿿]", text) else "English"


@dataclass
class GenerationResult:
    """代表一次 LLM 生成的結果。"""
    text: str              # 生成的回答
    messages: list         # 更新後的對話歷史（user + assistant，不含 system）
    model: str             # 使用的模型名稱


def generate(
    prompt: dict,
    model: str = "gemma3",
    format_type: Union[str, dict] = "",
    messages: Optional[list] = None,
    base_url: str = "http://localhost:11434",
) -> GenerationResult:
    """呼叫 Ollama /api/chat 生成回答。

    Args:
        prompt: 由 prompt_builder 產生的 dict，包含 "system" 和 "user" 兩個 key。
        model: Ollama 模型名稱。
        format_type: 輸出格式 — "" 純文字、"json" 一般 JSON mode、
                     或 JSON Schema dict 走 Ollama structured output
                     （token-level grammar constraint）。
        messages: 前幾輪對話歷史（user + assistant role，不含 system）。
        base_url: Ollama API 的 base URL。

    Returns:
        GenerationResult: 包含生成文字、更新後的對話歷史、模型名稱。

    Raises:
        ConnectionError: 無法連線到 Ollama。
        RuntimeError: API 回傳錯誤。
    """
    url = f"{base_url}/api/chat"

    # Anchor language + scope on every turn. Re-injected per-turn (NOT stored
    # in history) to fight multi-turn drift — small models forget the persona
    # rules after a few rounds of user pressure, so we keep echoing the
    # constraints right next to the latest user message.
    lang = _detect_language(prompt["user"])
    user_content = (
        f"{prompt['user']}\n\n"
        f"(Respond in {lang}. "
        f"Stay strictly on the topic of laptops sold at this booth — if the visitor asks about anything else, "
        f"decline politely in one sentence and invite a laptop question. "
        f"Do not generate facts, lists, or descriptions about non-laptop topics.)"
    )

    # System message always goes first; previous turns follow; new user turn last
    all_messages = [{"role": "system", "content": prompt["system"]}]
    if messages:
        all_messages.extend(messages)
    all_messages.append({"role": "user", "content": user_content})

    payload = {
        "model": model,
        "messages": all_messages,
        "stream": False,
    }

    if isinstance(format_type, dict):
        payload["format"] = format_type
        format_label = "schema"
    elif format_type == "json":
        payload["format"] = "json"
        format_label = "json"
    else:
        format_label = "text"

    print(f"[Generator] Calling Ollama ({model}) | Format: {format_label} | History: {len(messages or [])} turns")

    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
    except requests.ConnectionError:
        raise ConnectionError(
            f"無法連線到 Ollama ({url})。請確認 Ollama 正在運行。"
        )
    except requests.HTTPError as e:
        raise RuntimeError(f"Ollama API 錯誤：{e}")

    data = response.json()
    generated_text = data.get("message", {}).get("content", "").strip()

    print(f"[Generator] Done! Response length: {len(generated_text)} chars")

    # Append this turn to history for next call
    new_messages = list(messages or []) + [
        {"role": "user", "content": prompt["user"]},
        {"role": "assistant", "content": generated_text},
    ]

    return GenerationResult(
        text=generated_text,
        messages=new_messages,
        model=model,
    )
