"""Pydantic request models shared across the API routers."""

from pydantic import BaseModel, Field, field_validator


class GraphEdge(BaseModel):
    source: str
    target: str
    sourceHandle: str = ""
    targetHandle: str = ""


class GraphNode(BaseModel):
    id: str
    type: str
    params: dict = {}


# 一個 graph 最多容納的節點數。Batch eval 跑 N cases × M nodes，
# 兩邊都不設上限的話一次請求就能把 Ollama 跑爆。
_MAX_GRAPH_NODES = 100


class ExecuteRequest(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]

    @field_validator("nodes")
    @classmethod
    def _limit_nodes(cls, v):
        if len(v) > _MAX_GRAPH_NODES:
            raise ValueError(f"too many nodes (max {_MAX_GRAPH_NODES})")
        return v


class ChatQueryRequest(BaseModel):
    # 4000 字夠長到塞段落，又能擋自殘式 DoS。
    message: str = Field(..., max_length=4000)


class ChatProfileRequest(BaseModel):
    name: str
    # The full chat graph; required now that chat runs the graph end-to-end.
    graph: dict


class ActivateProfileRequest(BaseModel):
    name: str


class BatchEvalScope(BaseModel):
    mode: str  # "all" | "category" | "ids"
    category: str | None = None
    case_ids: list[str] | None = None

    @field_validator("case_ids")
    @classmethod
    def _limit_case_ids(cls, v):
        if v is not None and len(v) > 50:
            raise ValueError("too many case_ids (max 50)")
        return v


class BatchEvalRequest(BaseModel):
    graph: ExecuteRequest
    scope: BatchEvalScope
    worst_k: int = Field(3, ge=1, le=20)
