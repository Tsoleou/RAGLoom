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
    # 每位訪客一條對話狀態（history / stage / intent）。前端進場產生一個
    # UUID，每輪都帶上。空字串 → 落到共用的 "default" session（相容退路，
    # 但會回到舊的跨會話污染行為）。長度設限擋掉 client 亂塞超長 key。
    session_id: str = Field("", max_length=100)


class ChatResetRequest(BaseModel):
    # 只清掉自己這條 session，不影響其他在線訪客。
    session_id: str = Field("", max_length=100)


class ChatProfileRequest(BaseModel):
    name: str
    # The full chat graph; required now that chat runs the graph end-to-end.
    graph: dict


class ActivateProfileRequest(BaseModel):
    name: str


class UnlockRequest(BaseModel):
    # Operator unlock passphrase. Capped to a sane length; never persisted.
    passphrase: str = Field(..., min_length=1, max_length=256)


class ChangePassphraseRequest(BaseModel):
    # Rotate the KB encryption passphrase. Neither value is persisted.
    old_passphrase: str = Field(..., min_length=1, max_length=256)
    new_passphrase: str = Field(..., min_length=8, max_length=256)


class KBDocumentRequest(BaseModel):
    # Inject a document by pasted text (alternative to multipart upload).
    filename: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1, max_length=2_000_000)


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
