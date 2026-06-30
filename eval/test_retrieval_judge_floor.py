"""
Retrieval Judge floor 的確定性單元測試（mock 掉 LLM，秒級）。

背景：query-log 分析發現小 judge（gemma3:4b）過度修剪——14 筆裡 7 筆把 5 個
chunk 全判 keep=false，且從沒保留全部，被清空的還偏偏是 top_score 最高的強檢索
query。所以 judge_retrieval 加了 floor：判完少於 N 個就照 score 由高到低補回，
保證下游永遠拿得到 context。

這支測試把 requests.post 換成可控的假回應（不碰 Ollama），驗證：
  - judge 全判 false → floor 補回 top-N（依 score）、verdict 翻成 keep 並標註
  - judge 留得比 floor 少 → 補到 floor，不重複已留的
  - judge 留得比 floor 多 → 不動
  - floor 自動 cap 在 candidate 數量（K < floor 不會炸）
  - LLM 失敗的 keep-everything 降級路徑不受影響

兩種跑法：
    pytest eval/test_retrieval_judge_floor.py
    python -m eval.test_retrieval_judge_floor
"""

import json

import core.retrieval_judge as rj
from core.chunker import Chunk
from core.retrieval_types import RetrievalResult


def _mk_results(n: int) -> list[RetrievalResult]:
    """n 筆 candidate，score 由高到低（1.0, 0.9, ...），方便驗證 floor 補回順序。"""
    out = []
    for i in range(n):
        score = round(1.0 - i * 0.1, 4)
        out.append(
            RetrievalResult(
                chunk=Chunk(text=f"chunk-{i}", metadata={"filename": f"f{i}.txt"}),
                score=score,
                distance=1.0 - score,
            )
        )
    return out


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return {"response": json.dumps(self._payload)}


def _patch_verdicts(monkeypatch, keep_flags: list[bool]):
    """讓假 LLM 對第 i 個 chunk 回 keep=keep_flags[i]。"""
    payload = {"verdicts": [{"i": i, "keep": k, "reason": "x"} for i, k in enumerate(keep_flags)]}

    def _fake_post(url, json=None, timeout=None):  # noqa: A002 - 對齊 requests 簽名
        return _FakeResp(payload)

    monkeypatch.setattr(rj.requests, "post", _fake_post)


def test_floor_restores_when_judge_drops_everything(monkeypatch):
    results = _mk_results(5)
    _patch_verdicts(monkeypatch, [False] * 5)

    kept, verdicts = rj.judge_retrieval("q", results, floor=3)

    # 全判 false，但 floor=3 → 補回 score 最高的 3 個（chunk-0/1/2）。
    assert [r.chunk.text for r in kept] == ["chunk-0", "chunk-1", "chunk-2"]
    # 被補回的 verdict 翻成 keep=true 並標註是 floor 救回。
    for i in (0, 1, 2):
        assert verdicts[i].keep is True
        assert "restored by floor" in verdicts[i].reason
    # 沒被補的維持 keep=false。
    assert verdicts[3].keep is False and verdicts[4].keep is False


def test_floor_tops_up_partial_keep(monkeypatch):
    results = _mk_results(5)
    # judge 只留最低分的 chunk-4；floor 應從被丟的裡補最高分的 0、1。
    _patch_verdicts(monkeypatch, [False, False, False, False, True])

    kept, verdicts = rj.judge_retrieval("q", results, floor=3)

    assert sorted(r.chunk.text for r in kept) == ["chunk-0", "chunk-1", "chunk-4"]
    assert len(kept) == 3
    # chunk-4 是 judge 原本就留的，reason 不應被改成 floor。
    assert "restored by floor" not in verdicts[4].reason


def test_floor_does_not_trim_when_judge_keeps_enough(monkeypatch):
    results = _mk_results(5)
    _patch_verdicts(monkeypatch, [True, True, True, True, False])

    kept, _ = rj.judge_retrieval("q", results, floor=3)

    # judge 留 4 個 > floor，不該動。
    assert len(kept) == 4
    assert [r.chunk.text for r in kept] == ["chunk-0", "chunk-1", "chunk-2", "chunk-3"]


def test_floor_caps_at_candidate_count(monkeypatch):
    results = _mk_results(2)
    _patch_verdicts(monkeypatch, [False, False])

    kept, _ = rj.judge_retrieval("q", results, floor=3)

    # 只有 2 個 candidate，floor=3 → cap 在 2，全補回，不爆。
    assert len(kept) == 2


def test_floor_zero_allows_empty(monkeypatch):
    results = _mk_results(5)
    _patch_verdicts(monkeypatch, [False] * 5)

    kept, _ = rj.judge_retrieval("q", results, floor=0)

    # 明確關掉 floor → 維持舊行為（可清空）。
    assert kept == []


def test_llm_failure_keeps_everything(monkeypatch):
    results = _mk_results(5)

    def _boom(url, json=None, timeout=None):  # noqa: A002
        raise rj.requests.ConnectionError("down")

    monkeypatch.setattr(rj.requests, "post", _boom)

    kept, verdicts = rj.judge_retrieval("q", results, floor=3)

    # LLM 掛掉走 keep-everything 降級，floor 不改變這條路徑。
    assert len(kept) == 5
    assert all(v.keep for v in verdicts)


if __name__ == "__main__":  # 免 pytest 的快速跑法
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
