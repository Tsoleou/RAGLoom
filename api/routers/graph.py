"""Graph endpoints: node-types catalogue, default graph, sync execute, and the
live WebSocket execution stream."""

import asyncio
import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.auth import _TOKEN_HEADER, _settings, is_same_origin
from api.default_graph import _default_chat_graph
from api.engine import execute_graph
from api.node_registry import get_node_types_json
from api.schemas import ExecuteRequest

router = APIRouter()


@router.get("/api/node-types")
def get_node_types():
    """回傳所有可用的節點類型定義。"""
    return get_node_types_json()


@router.get("/api/default-graph")
def get_default_graph():
    """Return the server's default pipeline graph (ingest + query chains).

    Single source of truth: the Editor canvas fetches this on mount, and the
    chat path uses the same builder when a profile carries no saved graph.
    """
    return _default_chat_graph()


@router.post("/api/execute")
def execute(req: ExecuteRequest):
    """同步執行 graph，回傳所有節點的結果。"""
    nodes = [n.model_dump() for n in req.nodes]
    edges = [e.model_dump() for e in req.edges]

    results = execute_graph(nodes, edges)
    return results


@router.websocket("/api/ws/execute")
async def ws_execute(ws: WebSocket):
    """WebSocket 端點，即時推送每個節點的執行狀態。

    Client 送出 JSON: {"nodes": [...], "edges": [...]}
    Server 逐步推送: {"nodeId": "xxx", "status": "running|done|error", "preview": "..."}
    最後推送: {"type": "complete", "results": {...}}
    """
    # 服務模式同源放行（與 HTTP middleware 同邏輯）；admin editor 在 / 服務時
    # 開的 ws 是同源，不帶 token 也能連。跨 origin（dev proxy）仍需 token。
    expected = _settings.api_local_token
    if expected and not is_same_origin(ws.headers):
        provided = ws.headers.get(_TOKEN_HEADER, "")
        if not secrets.compare_digest(provided, expected):
            await ws.close(code=4401)
            return

    await ws.accept()

    try:
        raw = await ws.receive_text()
        data = json.loads(raw)

        nodes = data.get("nodes", [])
        edges = data.get("edges", [])

        loop = asyncio.get_event_loop()

        # 狀態更新的 callback（從同步 executor 呼叫）
        status_queue: asyncio.Queue = asyncio.Queue()

        def on_status(node_id: str, status: str, preview: str = "") -> None:
            loop.call_soon_threadsafe(
                status_queue.put_nowait,
                {"nodeId": node_id, "status": status, "preview": preview},
            )

        # 在 thread pool 中執行 graph（因為 executor 是同步的）
        async def run_graph():
            return await loop.run_in_executor(
                None,
                lambda: execute_graph(nodes, edges, on_status=on_status),
            )

        # 同時推送狀態和執行 graph
        task = asyncio.create_task(run_graph())

        # 持續讀取 status queue 並推送給 client
        while not task.done():
            try:
                msg = await asyncio.wait_for(status_queue.get(), timeout=0.1)
                await ws.send_json(msg)
            except asyncio.TimeoutError:
                continue

        # 推送 queue 中剩餘的狀態
        while not status_queue.empty():
            msg = status_queue.get_nowait()
            await ws.send_json(msg)

        results = task.result()
        await ws.send_json({"type": "complete", "results": results})

    except WebSocketDisconnect:
        print("[Server] WebSocket client disconnected")
    except Exception as e:
        try:
            await ws.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
        print(f"[Server] WebSocket error: {e}")
