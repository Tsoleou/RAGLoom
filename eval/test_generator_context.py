"""
Generator 的 context-window / history 防護單元測試（mock 掉 LLM，秒級）。

背景：generator 呼叫 Ollama /api/chat 時沒帶 options.num_ctx，Ollama 就默認
4096——而 booth 的 system block（persona + 6.8KB always-on reference 表）光 turn 0
就 ~3.5k token，幾乎不留空間給輸出，導致長回答在收尾句被截斷
（done_reason="length"）、超量輸入被默默丟掉。多輪 history 又無上限累積，會把
視窗持續填回去。修法：generate() 明確帶 num_ctx（預設 8192）+ 把 history 裁到最近
N 則（餵進去的、寫回去的都裁）。

這支測試攔截 requests.post 檢查送出的 payload，不碰 Ollama，驗證：
  - payload 一定帶 options.num_ctx（可覆寫）
  - 餵給模型的 history 被裁到 history_limit
  - 回傳的 new_messages（會被寫回 session）也被裁，不會無上限長大
  - history_limit=0 關掉裁剪

兩種跑法：
    pytest eval/test_generator_context.py
    python -m eval.test_generator_context
"""

import core.generator as gen


class _FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"message": {"content": "ok"}}


def _capture(monkeypatch) -> dict:
    """攔截 requests.post，把送出的 payload 存進 captured 回傳。"""
    captured = {}

    def _fake_post(url, json=None, timeout=None):  # noqa: A002 - 對齊 requests 簽名
        captured["url"] = url
        captured["payload"] = json
        return _FakeResp()

    monkeypatch.setattr(gen.requests, "post", _fake_post)
    return captured


def _mk_history(pairs: int) -> list:
    """pairs 組 (user, assistant) 共 2*pairs 則訊息。"""
    msgs = []
    for i in range(pairs):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    return msgs


def test_num_ctx_always_sent_default_8192(monkeypatch):
    cap = _capture(monkeypatch)
    gen.generate(prompt={"system": "s", "user": "q"})
    assert cap["payload"]["options"]["num_ctx"] == 8192


def test_num_ctx_override(monkeypatch):
    cap = _capture(monkeypatch)
    gen.generate(prompt={"system": "s", "user": "q"}, num_ctx=16384)
    assert cap["payload"]["options"]["num_ctx"] == 16384


def test_history_trimmed_into_payload(monkeypatch):
    cap = _capture(monkeypatch)
    gen.generate(prompt={"system": "s", "user": "q"}, messages=_mk_history(10), history_limit=12)

    sent = cap["payload"]["messages"]
    # [system] + last 12 history msgs + [new user] = 14
    assert sent[0]["role"] == "system"
    assert len(sent) == 1 + 12 + 1
    # The oldest pair (u0/a0) must have been dropped; newest kept.
    history_part = sent[1:-1]
    assert {"role": "user", "content": "u0"} not in history_part
    assert {"role": "assistant", "content": "a9"} in history_part


def test_returned_history_is_bounded(monkeypatch):
    _capture(monkeypatch)
    # Feed a long session; the returned messages (written back to session) must
    # stay capped so it can't grow unbounded across turns.
    result = gen.generate(
        prompt={"system": "s", "user": "newq"}, messages=_mk_history(10), history_limit=12
    )
    assert len(result.messages) == 12
    # The just-asked turn is always present at the tail.
    assert result.messages[-2] == {"role": "user", "content": "newq"}
    assert result.messages[-1] == {"role": "assistant", "content": "ok"}


def test_history_limit_zero_disables_trimming(monkeypatch):
    _capture(monkeypatch)
    result = gen.generate(
        prompt={"system": "s", "user": "newq"}, messages=_mk_history(10), history_limit=0
    )
    # 20 prior + 2 new = 22, nothing dropped.
    assert len(result.messages) == 22


if __name__ == "__main__":  # 免 pytest 的快速跑法
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
