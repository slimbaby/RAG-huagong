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
    # Query optimization results
    optimized_query: Optional[str]
    query_type: Optional[str]
    intent: Optional[str]  # "kb_query" | "general"
    keywords: List[str]
    expanded_queries: List[str]
    # Retrieval confidence
    retrieval_confident: Optional[bool]
    # Vectorization
    query_vector: Optional[List[float]]
    # Retrieval results
    raw_results: List[Dict]
    filtered_results: List[Dict]
    # Generation
    context: Optional[str]
    answer: Optional[str]
    citations: List[Dict]
    # Error handling
    error: Optional[str]
    # Metadata
    metadata: Dict[str, Any]
