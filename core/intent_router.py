"""
Intent Router — classify a booth visitor's inquiry into one intent.

A trade-show booth is an INQUIRY setting, not a sales funnel: visitors hop
between question types (specs → comparison → suitability) turn by turn. This
module classifies each turn's message into one of a small set of inquiry
intents so a downstream DialogueFlow can pick the matching mini-script.

Routing is DYNAMIC: the intent is re-detected every turn (no sticky lock), so a
visitor switching topics is followed immediately. The cost is one small LLM
classification call per turn; that is the trade for topic-hopping support.

Returns "" when nothing clearly matches — the DialogueFlow then falls back to a
generic "understand & answer" flow rather than forcing a wrong script.
"""

import requests


# The default booth inquiry intents. `label` is the routing key (matched against
# DialogueFlow's scripts dict); `description` steers the classifier. Edit on the
# node to add/rename intents — keep labels in sync with the DialogueFlow scripts.
DEFAULT_INTENTS: list[dict] = [
    {"label": "spec", "description": "詢問某台機型的特定規格（CPU、顯卡、螢幕、重量、電池等）"},
    {"label": "recommend", "description": "請求依需求推薦機型（想要輕便/效能/某用途，推薦哪台）"},
    {"label": "compare", "description": "比較兩台以上機型的差異"},
    {"label": "suitability", "description": "問某台適不適合某個用途（能不能剪片、跑得動遊戲嗎）"},
]


def _format_intents(intents: list[dict]) -> tuple[str, set[str]]:
    """Render the intent menu for the prompt and return the valid label set."""
    lines = []
    labels: set[str] = set()
    for it in intents:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label", "")).strip()
        if not label:
            continue
        desc = str(it.get("description", "")).strip()
        labels.add(label)
        lines.append(f"- {label}: {desc}" if desc else f"- {label}")
    return "\n".join(lines), labels


def classify_intent(
    query: str,
    intents: list[dict] | None = None,
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
) -> str:
    """Classify `query` into one intent label, or "" if none clearly matches.

    Returns "" on empty input, no menu, error, or an out-of-menu answer — the
    safe default is "no opinion", letting DialogueFlow use its generic fallback.
    """
    if not query or not query.strip():
        return ""
    menu, labels = _format_intents(intents or DEFAULT_INTENTS)
    if not labels:
        return ""

    full_prompt = (
        "You are an inquiry classifier for a product booth assistant. Read the "
        "visitor's message and pick the SINGLE best-matching intent label.\n\n"
        f"Available intents:\n{menu}\n\n"
        "Rules:\n"
        "1. Answer with ONLY the intent label token, nothing else.\n"
        "2. If none clearly matches (greeting, small talk, unrelated), answer NONE.\n"
        "3. Do not invent labels — use only the labels listed above.\n\n"
        f"[Visitor message]: {query.strip()}\n\n"
        "intent:"
    )
    url = f"{base_url}/api/generate"
    payload = {"model": model, "prompt": full_prompt, "stream": False}
    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
    except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as e:
        print(f"[IntentRouter] LLM call failed: {e}")
        return ""

    raw = response.json().get("response", "").strip()
    token = raw.split()[0].strip(".,;:\"'`()[]{}").lower() if raw else ""
    if not token or token == "none" or token not in labels:
        print(f"[IntentRouter] no match (raw='{raw[:40]}')")
        return ""
    print(f"[IntentRouter] intent='{token}' (raw='{raw[:40]}')")
    return token
