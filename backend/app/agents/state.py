"""
LangGraph状态定义
"""

from typing import TypedDict, Annotated, Any, Literal
from operator import add


class RAGState(TypedDict):
    """RAG查询状态 - 用于RAG流程编排（graph.py 以 StateGraph(RAGState) 绑定，
    节点返回 schema 之外的键会被 LangGraph 拒绝/丢弃，新增状态字段必须先在此声明）"""
    # ── 运行时依赖注入（graph._make_wrapper 写入；非业务数据，不落 trace）──
    _deps: dict[str, Any] | None
    _token_queue: Any | None  # asyncio.Queue, 流式 token 传递
    user_query: str
    session_id: str | None
    task_id: str | None
    trace_id: str | None
    # ── RBAC 权限上下文 ──
    # 当前用户的可见 doc_id 集合；None 表示 admin 看全部；空 set 表示无任何可见
    allowed_doc_ids: set[str] | None
    # 用户可见 scope 列表（None = admin）；供 prompt 兜底使用
    user_visible_scopes: list[str] | None
    # Query optimization results
    optimized_query: str | None
    query_type: str | None
    intent: str | None # "kb_query", "general"]
    keywords: list[str]
    expanded_queries: list[str]
    sub_queries: list[str]  # 比较类查询分解出的子查询列表
    # Retrieval confidence
    retrieval_confident: bool | None
    # Vectorization
    query_vector: list[float] | None
    # Retrieval results
    raw_results: list[dict[str, Any]]
    filtered_results: list[dict[str, Any]]
    # ── scope 兜底提示 ──
    # 检索为空时，其他 scope 中的相似结果归属的 scope 名称（供 prompt 提示用户）
    denied_scope_hints: list[str]
    # Generation
    context: str | None
    answer: str | None
    citations: list[dict[str, Any]]
    # Iterative retrieval (evidence judge)
    iteration_count: int
    accumulated_results: list[dict[str, Any]]
    need_more_evidence: bool
    judge_note: str | None
    # Error handling
    error: str | None
    # Metadata
    metadata: dict[str, Any]
    # ── 工具调用（generate 节点工具循环 / tool_call 节点使用）──
    tools_to_call: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]


class AgentState(RAGState):
    """通用Agent状态 - 用于未来多Agent协作。

    继承 RAGState：agent 图与 RAG 图共用同一批节点（query_optimize/retrieval/
    generate...），节点写入的全部是 RAG 键，schema 必须是 RAGState 的超集，
    否则绑定 TypedDict 后节点更新会被 LangGraph 拒绝。
    """
    messages: Annotated[list[dict[str, Any]], add]
    current_agent: str
    task_type: str
    final_answer: str | None