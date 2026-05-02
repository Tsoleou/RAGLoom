"""
Graph 執行引擎。

接收前端傳來的 graph（nodes + edges），拓撲排序後依序執行每個節點。
透過 callback 回報執行狀態，供 WebSocket 即時推送。
"""

from collections import defaultdict, deque
from typing import Any, Callable

from api.executors import EXECUTORS
from core.guardrail import GuardrailBlocked
from core.scope_gate import ScopeBlocked


# Status constants
STATUS_WAITING = "waiting"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_BLOCKED = "blocked"


def topological_sort(nodes: list[dict], edges: list[dict]) -> list[str]:
    """對 graph 做拓撲排序，回傳節點 ID 的執行順序。

    Args:
        nodes: [{"id": "node_1", "type": "loader", ...}, ...]
        edges: [{"source": "node_1", "target": "node_2", "sourceHandle": "documents", "targetHandle": "documents"}, ...]

    Returns:
        list[str]: 按執行順序排列的 node IDs

    Raises:
        ValueError: 如果 graph 有循環依賴
    """
    node_ids = {n["id"] for n in nodes}
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adjacency: dict[str, list[str]] = defaultdict(list)

    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if src in node_ids and tgt in node_ids:
            adjacency[src].append(tgt)
            in_degree[tgt] += 1

    # Kahn's algorithm
    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        nid = queue.popleft()
        order.append(nid)
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(node_ids):
        raise ValueError("Graph 有循環依賴，無法執行")

    return order


def build_input_map(
    node_id: str,
    edges: list[dict],
    outputs: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """收集某個節點的所有 input 資料。

    根據 edges 找到所有指向 node_id 的連線，
    從上游節點的 outputs 中取出對應的資料。
    """
    inputs: dict[str, Any] = {}
    for edge in edges:
        if edge["target"] == node_id:
            source_id = edge["source"]
            # sourceHandle = output port name of source node
            # targetHandle = input port name of target node
            source_handle = edge.get("sourceHandle", "")
            target_handle = edge.get("targetHandle", "")

            if source_id in outputs and source_handle in outputs[source_id]:
                inputs[target_handle] = outputs[source_id][source_handle]

    return inputs


def execute_graph(
    nodes: list[dict],
    edges: list[dict],
    on_status: Callable[[str, str, str], None] | None = None,
) -> dict[str, Any]:
    """執行整個 graph。

    Args:
        nodes: 前端傳來的節點陣列，每個包含 id, type, params
        edges: 前端傳來的連線陣列
        on_status: callback(node_id, status, preview) 用於即時通知

    Returns:
        dict: {node_id: {"status": "done", "preview": "..."}, ...}
    """
    def notify(node_id: str, status: str, preview: str = "") -> None:
        if on_status:
            on_status(node_id, status, preview)

    # 拓撲排序
    order = topological_sort(nodes, edges)

    # Build node lookup
    node_map = {n["id"]: n for n in nodes}

    # 儲存每個節點的 output
    outputs: dict[str, dict[str, Any]] = {}
    results: dict[str, dict] = {}

    # 先把所有節點標記為 waiting
    for nid in order:
        notify(nid, STATUS_WAITING)

    # 依序執行
    for nid in order:
        node = node_map[nid]
        node_type = node.get("type", "")
        params = node.get("params", {})

        executor = EXECUTORS.get(node_type)
        if not executor:
            notify(nid, STATUS_ERROR, f"Unknown node type: {node_type}")
            results[nid] = {"status": STATUS_ERROR, "preview": f"Unknown type: {node_type}"}
            continue

        # 收集 inputs
        inputs = build_input_map(nid, edges, outputs)

        notify(nid, STATUS_RUNNING)

        try:
            output = executor(inputs, params)
            # 分離 _preview 和實際資料
            preview = output.pop("_preview", "")
            outputs[nid] = output
            results[nid] = {"status": STATUS_DONE, "preview": preview}
            notify(nid, STATUS_DONE, preview)
        except (GuardrailBlocked, ScopeBlocked) as blocked:
            # Short-circuit: mark the gate node itself as "blocked", mirror
            # the refusal onto any result_display nodes, stop execution.
            # Same handling for both gates (brand keyword + scope threshold).
            blocked_preview = f"⊘ BLOCKED\nMatched: {blocked.matched_keyword}\n\n{blocked.refusal_message}"
            results[nid] = {"status": STATUS_BLOCKED, "preview": blocked_preview}
            notify(nid, STATUS_BLOCKED, blocked_preview)

            for other_nid in order:
                if other_nid == nid or other_nid in results:
                    continue
                if node_map[other_nid].get("type") == "result_display":
                    results[other_nid] = {
                        "status": STATUS_BLOCKED,
                        "preview": blocked.refusal_message,
                    }
                    notify(other_nid, STATUS_BLOCKED, blocked.refusal_message)

            gate_kind = "guardrail" if isinstance(blocked, GuardrailBlocked) else "scope_gate"
            print(f"[Engine] Pipeline short-circuited by {gate_kind} at '{nid}' (matched: {blocked.matched_keyword})")
            break
        except Exception as e:
            error_msg = str(e)
            results[nid] = {"status": STATUS_ERROR, "preview": error_msg}
            notify(nid, STATUS_ERROR, error_msg)
            print(f"[Engine] Node '{nid}' failed: {error_msg}")
            break

    return results
