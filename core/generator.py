"""
LLM 生成模組。

呼叫 Ollama API 生成回答，支援 text / json 輸出格式與多輪對話 context。
"""

import requests
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class GenerationResult:
    """代表一次 LLM 生成的結果。"""
    text: str              # 生成的回答
    context: list          # Ollama 的 context tokens（用於多輪對話）
    model: str             # 使用的模型名稱


def generate(
    prompt: dict,
    model: str = "gemma3",
    format_type: str = "",
    context: Optional[list] = None,
    base_url: str = "http://localhost:11434",
) -> GenerationResult:
    """呼叫 Ollama API 生成回答。

    Args:
        prompt: 由 prompt_builder 產生的 dict，包含 "system" 和 "user" 兩個 key。
        model: Ollama 模型名稱。
        format_type: 輸出格式 — ""（純文字）或 "json"。
        context: 前一輪對話的 Ollama context tokens（用於多輪對話延續）。
        base_url: Ollama API 的 base URL。

    Returns:
        GenerationResult: 包含生成文字、context tokens、模型名稱。

    Raises:
        ConnectionError: 無法連線到 Ollama。
        RuntimeError: API 回傳錯誤。
    """
    url = f"{base_url}/api/generate"

    # 組合 system + user 成完整 prompt
    full_prompt = f"{prompt['system']}\n\n[User Request]: {prompt['user']}"

    payload = {
        "model": model,
        "prompt": full_prompt,
        "stream": False,
    }

    if format_type == "json":
        payload["format"] = "json"

    if context:
        payload["context"] = context

    print(f"[Generator] Calling Ollama ({model}) | Format: {format_type or 'text'}")

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
    generated_text = data.get("response", "").strip()
    new_context = data.get("context", [])

    print(f"[Generator] Done! Response length: {len(generated_text)} chars")

    return GenerationResult(
        text=generated_text,
        context=new_context,
        model=model,
    )
