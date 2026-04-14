"""
Product Selector.

Given a user query and an always-on product reference (e.g., a comparison
table), ask a small LLM to decide whether the query targets a specific
product and, if so, return its canonical product_id. Output is used by
Retriever to apply a metadata filter, turning a broad semantic search into
a focused single-product retrieval.

Returns an empty string when:
  - The reference is empty
  - The query is ambiguous / broad (e.g., comparison queries)
  - The LLM answers "NONE"
"""

import requests


SYSTEM_INSTRUCTIONS = (
    "You are a product ID classifier. Given a user question and a product "
    "reference table, decide which single product the user is asking about.\n\n"
    "Rules:\n"
    "1. The reference table has a `product_id` column — your answer MUST be the exact value from that column (e.g. `starforge_x1`), not the series name or model name.\n"
    "2. Respond with ONLY the product_id token, nothing else. No punctuation, no quotes, no explanation.\n"
    "3. If the question is ambiguous, about multiple products, or a comparison, respond with NONE.\n"
    "4. If no product clearly matches, respond with NONE.\n"
    "5. Do not invent IDs. Use only IDs that appear in the `product_id` column."
)


def select_product(
    query: str,
    reference_text: str,
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
) -> str:
    """Classify a query to a single product_id using reference material.

    Returns the product_id string, or "" if none/ambiguous/error.
    """
    if not query or not query.strip():
        return ""
    if not reference_text or not reference_text.strip():
        return ""

    full_prompt = (
        f"{SYSTEM_INSTRUCTIONS}\n\n"
        f"[Product Reference]:\n{reference_text.strip()}\n\n"
        f"[Question]: {query.strip()}\n\n"
        f"product_id:"
    )

    url = f"{base_url}/api/generate"
    payload = {"model": model, "prompt": full_prompt, "stream": False}

    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()
    except (requests.ConnectionError, requests.HTTPError) as e:
        print(f"[ProductSelector] LLM call failed: {e}")
        return ""

    raw = response.json().get("response", "").strip()
    # Normalize: take first token, strip punctuation, lowercase
    token = raw.split()[0] if raw else ""
    token = token.strip(".,;:\"'`()[]{}").lower()

    if not token or token == "none":
        print(f"[ProductSelector] No match (raw='{raw[:60]}')")
        return ""

    print(f"[ProductSelector] Selected product_id='{token}' (raw='{raw[:60]}')")
    return token
