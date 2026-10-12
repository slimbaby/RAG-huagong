"""
LangGraph状态定义
"""

from typing import TypedDict, Annotated, List, Dict, Optional, Any
from operator import add
import operator


class RAGState(TypedDict):
    """RAG查询状态 - 用于RAG流程编排（graph.py 以 StateGraph(RAGState) 绑定，
    节点返回 schema 之外的键会被 LangGraph 拒绝/丢弃，新增状态字段必须先在此声明）"""
    # ── 运行时依赖注入（graph._make_wrapper 写入；非业务数据，不落 trace）──
    _deps: Optional[Dict[str, Any]]
    _token_queue: Optional[Any]  # asyncio.Queue, 浫式 token 传递
    user_query: str
    session_id: Optional[str]
    task_id: Optional[str]
    trace_id: Optional[str]
    # ── RBAC 权限上下文 ──
    # 当前用户的可见 doc_id 集合；None 表示 admin 看全部；空 set 表示无任何可见
    allowed_doc_ids: Optional[Any]  # Optional[set[str]]
    # 用户可见 scope 列表（None = admin）；供 prompt 兜底使用
    user_visible_scopes: Optional[List[str]]
    # Query optimization results
    optimized_query: Optional[str]
    query_type: Optional[str]
    intent: Optional[str]  # "kb_query" | "general"
    keywords: List[str]
    expanded_queries: List[str]
    sub_queries: List[str]  # 比较类查询分解出的子查询列表
    # Retrieval confidence
    retrieval_confident: Optional[bool]
    # Vectorization
    query_vector: Optional[List[float]]
    # Retrieval results
    raw_results: List[Dict]
    filtered_results: List[Dict]
    # ── scope 兜底提示 ──
    # 检索为空时，其他 scope 中的相似结果归属的 scope 名称（供 prompt 提示用户）
    denied_scope_hints: List[str]
    # Generation
    context: Optional[str]
    answer: Optional[str]
    citations: List[Dict]
    # Iterative retrieval (evidence judge)
    iteration_count: int
    accumulated_results: List[Dict]
    need_more_evidence: bool
    judge_note: Optional[str]
    # Error handling
    error: Optional[str]
    # Metadata
    metadata: Dict[str, Any]
    # ── 工具调用（generate 节点工具循环 / tool_call 节点使用）──
    tools_to_call: List[Dict]
    tool_results: List[Dict]


class AgentState(RAGState):
    """通用Agent状态 - 用于未来多Agent协作。

    继承 RAGState：agent 图与 RAG 图共用同一批节点（query_optimize/retrieval/
    generate...），节点写入的全部是 RAG 键，schema 必须是 RAGState 的超集，
    否则绑定 TypedDict 后节点更新会被 LangGraph 拒绝。
    """
    messages: Annotated[List[Dict], add]
    current_agent: str
    task_type: str
    final_answer: Optional[str]
