"""
Dialogue Flow — guided multi-turn inquiry scripts for a product booth.

A trade-show booth is an INQUIRY setting: visitors ask about specs, ask for a
recommendation, compare models, or check whether a model suits their use. Each
inquiry type has its own short script (a "mini funnel"): some are single-turn
(spec lookup), some need a clarify-then-answer flow (recommend / suitability).

On every turn the flow does two separable things:
  1. CONTROL — decide whether the current stage's exit criteria are satisfied
     by what the visitor just said, and if so advance to the next stage.
  2. CONTENT — emit a per-turn directive (the active stage's instruction) that
     gets injected into the system prompt to steer how the bot replies.

Control is deliberately separated from content: small models like gemma3:4b
can't reliably self-manage a multi-stage script from a free-form prompt, so
advancement is an isolated YES/NO signal, not the main generator's job.

Routing is DYNAMIC (see core/intent_router): the active script is chosen by the
incoming `intent` each turn. When the intent changes from the previous turn, the
stage resets to 0 (we entered a new script). When no intent is wired/matched,
the flat `stages` fallback runs — so the node also works standalone.

v1 ("soft") decides advancement with a single constrained LLM call
(`decide_advance`). That function is the ONE place to harden later: swap the LLM
gate for deterministic code without touching the node, executor, or chat
plumbing.
"""

import json
from dataclasses import dataclass

import requests


@dataclass
class Stage:
    """One stage of a script.

    name         — short label shown in traces / the node preview.
    goal         — what this stage is trying to accomplish (fed to the gate).
    advance_when — natural-language exit criteria the soft gate evaluates.
    instruction  — steering text injected into the system prompt this turn.
    """
    name: str
    goal: str
    advance_when: str
    instruction: str


# Per-intent booth inquiry scripts. Labels match core/intent_router's intents.
# Single-stage scripts (spec) are effectively one-shot — they never advance.
DEFAULT_SCRIPTS: dict[str, list[dict]] = {
    "spec": [
        {
            "name": "回答規格",
            "goal": "直接回答訪客問的規格項目",
            "advance_when": "（單關，通常不前進）",
            "instruction": "直接、明確地回答訪客問的那項規格，只用知識庫的事實。答完可順帶問他還想了解哪一項，不要倒一長串無關規格。",
        },
    ],
    "recommend": [
        {
            "name": "探詢需求",
            "goal": "問出推薦所需的關鍵條件（用途、偏好輕便或效能、螢幕）",
            "advance_when": "已掌握至少一項明確需求，足以推薦具體機型",
            "instruction": "親切追問一個最關鍵的條件（主要用途、或偏輕便還是效能）。一次問一個就好。",
        },
        {
            "name": "推薦機型",
            "goal": "依需求推薦一到兩台並說明為何適合",
            "advance_when": "（最後一關）",
            "instruction": "依掌握到的需求，從知識庫推薦最合適的一到兩台，用一兩個關鍵規格說明為什麼適合。只講最打動人的亮點。",
        },
    ],
    "compare": [
        {
            "name": "確認比較對象",
            "goal": "確認訪客想比較哪幾台",
            "advance_when": "已知道要比較的機型（訪客已點名兩台以上）",
            "instruction": "若訪客沒講清楚要比哪幾台，先親切確認對象；若已明確，直接進入比較。",
        },
        {
            "name": "列出差異",
            "goal": "列出關鍵差異並給適配建議",
            "advance_when": "（最後一關）",
            "instruction": "用知識庫事實列出這幾台最有感的 2-3 個差異維度，最後給一句「看你比較在意X就選哪台」的建議。",
        },
    ],
    "suitability": [
        {
            "name": "確認用途",
            "goal": "釐清訪客要拿來做什麼、到什麼程度",
            "advance_when": "已了解訪客的具體用途與需求強度",
            "instruction": "確認訪客的具體用途細節（例如剪片是 1080p 還是 4K、遊戲是哪一類）。一句話問清楚。",
        },
        {
            "name": "判斷適配",
            "goal": "依規格判斷適不適合，不適合就改推更合適的",
            "advance_when": "（最後一關）",
            "instruction": "依知識庫規格誠實判斷這台適不適合該用途：適合就點出支撐的關鍵規格；不夠的話坦白講並改推更合適的機型。",
        },
    ],
}


# Flat fallback when no intent is wired/matched — a generic "understand &
# answer" inquiry turn. Also the default for the node's `stages` param so the
# node is useful standalone (without an IntentRouter upstream).
DEFAULT_STAGES: list[dict] = [
    {
        "name": "理解並回答",
        "goal": "理解訪客的詢問並用知識庫如實回答",
        "advance_when": "（單關）",
        "instruction": "親切、簡潔地回答訪客的問題，只用知識庫的事實。答完可問他還想了解什麼。",
    },
]


def _to_stages(items) -> list[Stage]:
    """Coerce a list of stage dicts into Stage objects, dropping non-dicts."""
    stages: list[Stage] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        stages.append(Stage(
            name=str(it.get("name", "")).strip(),
            goal=str(it.get("goal", "")).strip(),
            advance_when=str(it.get("advance_when", "")).strip(),
            instruction=str(it.get("instruction", "")).strip(),
        ))
    return stages


def parse_stages(raw) -> list[Stage]:
    """Parse the flat `stages` param (JSON list of dicts) into Stage objects.

    Falls back to DEFAULT_STAGES on empty / invalid input so a typo in the node
    textarea can never crash a live chat turn.
    """
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            items = json.loads(raw)
        except (ValueError, TypeError):
            print("[DialogueFlow] invalid stages JSON — falling back to default")
            items = DEFAULT_STAGES
    else:
        items = DEFAULT_STAGES
    stages = _to_stages(items)
    return stages or _to_stages(DEFAULT_STAGES)


def parse_scripts(raw) -> dict[str, list[Stage]]:
    """Parse the `scripts` param (JSON {intent_label: [stage dicts]}).

    Falls back to DEFAULT_SCRIPTS on empty / invalid input. Empty/malformed
    individual scripts are skipped. Returns {} only if everything is unusable
    (the executor then uses the flat `stages` fallback).
    """
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            print("[DialogueFlow] invalid scripts JSON — falling back to default")
            data = DEFAULT_SCRIPTS
    else:
        data = DEFAULT_SCRIPTS

    if not isinstance(data, dict):
        data = DEFAULT_SCRIPTS

    scripts: dict[str, list[Stage]] = {}
    for label, items in data.items():
        stages = _to_stages(items)
        if stages:
            scripts[str(label).strip()] = stages
    return scripts


_ADVANCE_SYSTEM = (
    "You are a conversation stage controller. You decide exactly ONE thing: has "
    "the CURRENT stage's exit criteria been satisfied by the conversation so "
    "far, so the assistant should move on to the next stage?\n"
    "Judge only against the stated criteria. When unsure, answer NO (it is "
    "always safe to stay in the current stage one more turn).\n"
    "Answer with ONLY 'YES' or 'NO'. No explanation, no punctuation."
)


def _format_history(messages, limit: int = 6) -> str:
    """Render the last `limit` user/assistant turns as a compact transcript."""
    lines: list[str] = []
    for m in (messages or [])[-limit:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = (m.get("content", "") or "").strip()
        if role in ("user", "assistant") and content:
            who = "Visitor" if role == "user" else "Assistant"
            lines.append(f"{who}: {content}")
    return "\n".join(lines) if lines else "(no prior turns)"


def decide_advance(
    stage: Stage,
    query: str,
    messages,
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
) -> bool:
    """Soft (LLM) gate: should we LEAVE `stage` after the visitor's latest line?

    Returns False on any error / empty input / unparseable output — staying in
    the current stage is the safe default. This is the SINGLE hardening point:
    replace the body with deterministic slot logic to make a script code-tracked
    instead of LLM-driven, with no change required anywhere else.
    """
    if not stage.advance_when or not (query and query.strip()):
        return False

    full_prompt = (
        f"{_ADVANCE_SYSTEM}\n\n"
        f"[Current stage]: {stage.name}\n"
        f"[Stage goal]: {stage.goal}\n"
        f"[Advance when]: {stage.advance_when}\n\n"
        f"[Conversation so far]:\n{_format_history(messages)}\n"
        f"Visitor (latest): {query.strip()}\n\n"
        f"Has the advance criteria been met? Answer YES or NO:"
    )
    url = f"{base_url}/api/generate"
    payload = {"model": model, "prompt": full_prompt, "stream": False}
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
    except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as e:
        print(f"[DialogueFlow] advance gate LLM call failed: {e}")
        return False

    raw = response.json().get("response", "").strip().lower()
    advanced = raw.startswith("yes")
    print(f"[DialogueFlow] gate: stage='{stage.name}' -> {'YES' if advanced else 'NO'} (raw='{raw[:40]}')")
    return advanced


def advance_stage(
    stages: list[Stage],
    current_index: int,
    query: str,
    messages,
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
) -> tuple[int, bool]:
    """Evaluate the gate and return (new_index, advanced).

    Never moves past the final stage (single-stage scripts never advance).
    Out-of-range incoming indices are clamped so corrupt session state heals.
    """
    if not stages:
        return 0, False
    idx = max(0, min(int(current_index), len(stages) - 1))
    if idx >= len(stages) - 1:
        return idx, False  # already at the last stage (or a one-shot script)
    if decide_advance(stages[idx], query, messages, model, base_url):
        return idx + 1, True
    return idx, False


def build_stage_directive(stages: list[Stage], index: int, script_label: str = "") -> str:
    """The per-turn instruction injected into the system prompt for the active
    stage. Returns "" when there are no stages (node becomes a pass-through).
    """
    if not stages:
        return ""
    idx = max(0, min(int(index), len(stages) - 1))
    stage = stages[idx]
    tag = f"{script_label} — " if script_label else ""
    return (
        f"[DIALOGUE FLOW — {tag}stage {idx + 1}/{len(stages)}: {stage.name}]\n"
        f"This turn's goal: {stage.goal}\n"
        f"How to act this turn: {stage.instruction}\n"
        "Stay in character as a warm trade-show product guide answering a "
        "visitor's inquiry. Never mention stages, scripts, or that you are "
        "following a flow."
    )
