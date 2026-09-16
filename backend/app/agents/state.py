"""
LangGraph状态定义
"""

from typing import TypedDict, Annotated, List, Dict, Optional, Any
from operator import add
import operator


class AgentState(TypedDict):
    """通用Agent状态 - 用于未来多Agent协作"""
    messages: Annotated[List[Dict], add]
    current_agent: str
    task_type: str
    session_id: Optional[str]
    trace_id: Optional[str]
    task_id: Optional[str]
    tools_to_call: List[Dict]
    tool_results: List[Dict]
    final_answer: Optional[str]
    metadata: Dict[str, Any]


class RAGState(TypedDict):
    """RAG查询状态 - 用于RAG流程编排"""
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
