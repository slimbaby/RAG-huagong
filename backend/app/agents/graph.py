"""
LangGraph图定义 - RAG和Agent工作流编排
"""

from typing import Dict, Any, Optional
import logging

from langgraph.graph import StateGraph, END

from .state import RAGState, AgentState
from .nodes import (
    query_optimize_node,
    vectorize_node,
    retrieval_node,
    reranker_node,
    generate_node,
    tool_call_node,
    should_use_tools,
    route_after_optimize,
    check_retrieval_confidence_node,
    evidence_judge_node,
    route_after_evidence_judge,
)
from ..tracing.tracer import SpanType

logger = logging.getLogger(__name__)

_NODE_SPAN_MAP = {
    "query_optimize": SpanType.QUERY_OPTIMIZE,
    "vectorize": SpanType.VECTOR_SEARCH,
    "retrieval": SpanType.RETRIEVAL,
    "reranker": SpanType.RETRIEVAL,
    "check_confidence": SpanType.RETRIEVAL,
    "evidence_judge": SpanType.LLM_CALL,
    "generate": SpanType.LLM_CALL,
    "tool_call": SpanType.TOOL_CALL,
}


def extract_span_outputs(node_name: str, updates: Dict, state: Dict = None) -> Dict:
    """提取每个节点的有意义输出，供前端展示"""
    if node_name == "query_optimize":
        return {
            "rewritten": updates.get("optimized_query", ""),
            "query_type": updates.get("query_type", ""),
            "intent": updates.get("intent", ""),
            "keywords": updates.get("keywords", []),
            "expanded_queries": updates.get("expanded_queries", []),
            "sub_queries": updates.get("sub_queries", []),
        }
    elif node_name == "retrieval":
        results = updates.get("filtered_results", [])
        preview = []
        for r in results[:5]:
            if isinstance(r, dict):
                preview.append({
                    "rank": r.get("rank", 0),
                    "source_file": r.get("metadata", {}).get("source_file", ""),
                    "page_number": r.get("metadata", {}).get("page_number", 0),
                    "content_type": r.get("metadata", {}).get("content_type", ""),
                    "vector_score": round(r.get("vector_score", 0), 4),
                    "bm25_score": round(r.get("bm25_score", 0), 4),
                    "hybrid_score": round(r.get("hybrid_score", 0), 4),
                    "content_preview": (r.get("content", "") or "")[:500],
                })
        return {"results": preview, "results_count": len(results)}
    elif node_name == "reranker":
        results = updates.get("filtered_results", [])
        preview = []
        for r in results[:5]:
            if isinstance(r, dict):
                preview.append({
                    "chunk_id": r.get("chunk_id", ""),
                    "reranker_score": round(r.get("reranker_score", 0), 4),
                    "content_preview": (r.get("content", "") or "")[:500],
                })
        return {"results": preview, "results_count": len(results)}
    elif node_name == "check_confidence":
        return {
            "retrieval_confident": updates.get("retrieval_confident", True),
        }
    elif node_name == "evidence_judge":
        return {
            "need_more_evidence": updates.get("need_more_evidence", False),
            "iteration_count": updates.get("iteration_count", 0),
            "judge_note": updates.get("judge_note", ""),
        }
    elif node_name == "generate":
        context = updates.get("context", "")
        answer = updates.get("answer", "")
        return {
            "prompt_preview": context[:500] if context else "",
            "response_preview": answer[:500] if answer else "",
        }
    return {"output_keys": list(updates.keys())}


def _make_wrapper(deps, node_fn, node_name: str = None):
    """创建节点包装函数，注入依赖、记录span并合并状态"""
    async def wrapper(state):
        state["_deps"] = deps or {}
        tracer = (deps or {}).get("tracer")
        span_type = _NODE_SPAN_MAP.get(node_name) if node_name else None

        # 将 task_id 写入日志上下文
        task_id = state.get("task_id", "")
        task_id_token = None
        try:
            from ..core.logging_context import task_id_var
            task_id_token = task_id_var.set(task_id)
        except Exception:
            pass

        if tracer and span_type:
            inputs = {k: v for k, v in state.items() if k != "_deps" and not k.startswith("query_vector")}
            # 只保留可序列化的简单值
            clean_inputs = {}
            for k, v in inputs.items():
                if isinstance(v, (str, int, float, bool, type(None))):
                    clean_inputs[k] = v
                elif isinstance(v, list) and len(v) <= 5:
                    clean_inputs[k] = v
                elif isinstance(v, dict):
                    clean_inputs[k] = {sk: sv for sk, sv in v.items() if isinstance(sv, (str, int, float, bool, type(None)))}
            with tracer.start_span(span_type, node_name or "", inputs=clean_inputs) as span:
                updates = await node_fn(state)
                span.outputs = extract_span_outputs(node_name, updates, state)
                if "error" in updates and updates["error"]:
                    span.status = "error"
                    span.error_message = updates["error"]
        else:
            updates = await node_fn(state)

        # 清除日志上下文中的 task_id
        if task_id_token is not None:
            try:
                task_id_var.reset(task_id_token)
            except Exception:
                pass

        merged = {**state, **updates}
        return merged
    return wrapper


def create_rag_graph(deps: Dict[str, Any] = None):
    """
    创建RAG查询图

    流程: query_optimize -> [route_intent] -> kb_query: vectorize -> retrieval -> reranker -> check_confidence -> evidence_judge -> [retrieve_more | generate]
                                            -> general: generate
    """
    # 绑定 RAGState TypedDict（而非裸 dict）：节点写入未声明的键会被拒绝，
    # 状态字段获得编译期文档与校验；新增字段先在 state.py 声明
    workflow = StateGraph(RAGState)

    workflow.add_node("query_optimize", _make_wrapper(deps, query_optimize_node, "query_optimize"))
    workflow.add_node("vectorize", _make_wrapper(deps, vectorize_node, "vectorize"))
    workflow.add_node("retrieval", _make_wrapper(deps, retrieval_node, "retrieval"))
    workflow.add_node("reranker", _make_wrapper(deps, reranker_node, "reranker"))
    workflow.add_node("check_confidence", _make_wrapper(deps, check_retrieval_confidence_node, "check_confidence"))
    workflow.add_node("evidence_judge", _make_wrapper(deps, evidence_judge_node, "evidence_judge"))
    workflow.add_node("generate", _make_wrapper(deps, generate_node, "generate"))

    workflow.set_entry_point("query_optimize")

    # 意图路由：kb_query 走检索流程，general 直接生成，business 走工具调用
    workflow.add_conditional_edges("query_optimize", route_after_optimize, {
        "kb_query": "vectorize",
        "general": "generate",
        "business": "generate",
    })

    workflow.add_edge("vectorize", "retrieval")
    workflow.add_edge("retrieval", "reranker")
    workflow.add_edge("reranker", "check_confidence")
    workflow.add_edge("check_confidence", "evidence_judge")
    workflow.add_conditional_edges("evidence_judge", route_after_evidence_judge, {
        "retrieve_more": "vectorize",
        "generate": "generate",
    })
    workflow.add_edge("generate", END)

    return workflow.compile()


def create_agent_graph(deps: Dict[str, Any] = None):
    """
    创建Agent图 - 支持工具调用的Agent工作流

    流程: query_optimize -> [route_intent] -> kb_query: vectorize -> retrieval -> reranker -> check_confidence -> [tool_call | generate]
                                           -> general: generate
    用于未来多Agent协作场景（ERP、CRM、采购等）
    """
    # AgentState 继承 RAGState（共用节点集），另含 messages 等 Agent 扩展字段
    workflow = StateGraph(AgentState)

    workflow.add_node("query_optimize", _make_wrapper(deps, query_optimize_node, "query_optimize"))
    workflow.add_node("vectorize", _make_wrapper(deps, vectorize_node, "vectorize"))
    workflow.add_node("retrieval", _make_wrapper(deps, retrieval_node, "retrieval"))
    workflow.add_node("reranker", _make_wrapper(deps, reranker_node, "reranker"))
    workflow.add_node("check_confidence", _make_wrapper(deps, check_retrieval_confidence_node, "check_confidence"))
    workflow.add_node("evidence_judge", _make_wrapper(deps, evidence_judge_node, "evidence_judge"))
    workflow.add_node("generate", _make_wrapper(deps, generate_node, "generate"))
    workflow.add_node("tool_call", _make_wrapper(deps, tool_call_node, "tool_call"))

    workflow.set_entry_point("query_optimize")

    # 意图路由
    workflow.add_conditional_edges("query_optimize", route_after_optimize, {
        "kb_query": "vectorize",
        "general": "generate",
        "business": "generate",
    })

    workflow.add_edge("vectorize", "retrieval")
    workflow.add_edge("retrieval", "reranker")
    workflow.add_edge("reranker", "check_confidence")
    workflow.add_edge("check_confidence", "evidence_judge")
    workflow.add_conditional_edges("evidence_judge", route_after_evidence_judge, {
        "retrieve_more": "vectorize",
        "generate": "generate",
    })
    workflow.add_edge("generate", END)

    return workflow.compile()
