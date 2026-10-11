"""StateGraph 绑定 RAGState TypedDict 后的运行时冒烟测试（2026-09-25）。

此前图绑定的是裸 dict，无任何状态校验；改为 StateGraph(RAGState) 后，
节点返回 schema 之外的键会被 LangGraph 拒绝——本测试用 stub 依赖真实
ainvoke 三条路径，验证 wrapper 返回的完整 merged 状态（含 _deps 私有通道）
在 TypedDict schema 下被接受，且条件路由 / 补检索回边行为不变：

1. kb_query 正常路径：检索 → 置信度 → 生成
2. 检索为空：evidence_judge 回边补检索一轮（最多 1 轮）→ generate 兜底
3. general 意图：条件边短路，完全不触发检索
"""

import asyncio

import pytest

from app.agents.graph import create_rag_graph
from app.services.query_rewriter import OptimizedQuery, QueryType


def _initial_state(question="反应釜温度上限是多少", **overrides):
    """与 chat.py initial_state 字段一致的最小状态"""
    state = {
        "user_query": question,
        "session_id": None,
        "task_id": "t-smoke",
        "allowed_doc_ids": None,
        "user_visible_scopes": None,
        "denied_scope_hints": [],
        "optimized_query": None,
        "query_type": None,
        "intent": None,
        "keywords": [],
        "expanded_queries": [],
        "sub_queries": [],
        "query_vector": None,
        "raw_results": [],
        "filtered_results": [],
        "retrieval_confident": None,
        "context": None,
        "answer": None,
        "citations": [],
        "error": None,
        "metadata": {},
        "tools_to_call": [],
        "tool_results": [],
    }
    state.update(overrides)
    return state


class StubEmbedding:
    async def embed_query(self, text):
        return [0.1] * 8


class StubStore:
    """记录调用次数的检索桩；返回结果的副本防止节点原地修改串用例"""

    def __init__(self, results):
        self.results = results
        self.calls = 0

    async def hybrid_search(self, **kwargs):
        self.calls += 1
        return [dict(r) for r in self.results]


class StubLLM:
    async def generate(self, prompt, **kwargs):
        return {"text": "stub answer", "usage": {}}


class StubRewriterGeneral:
    async def optimize(self, query):
        return OptimizedQuery(
            original=query, rewritten=query, expanded_queries=[],
            query_type=QueryType.FACTUAL, keywords=[], intent_confidence=0.9,
            optimization_notes=[], intent="general", usage={}, sub_queries=[],
        )


GOOD_RESULTS = [
    {
        "chunk_id": "c1",
        "content": "反应釜温度上限是200度",
        "metadata": {"source_file": "安全规程.pdf", "page_number": 3, "content_type": "text"},
        "vector_score": 0.82,
        "bm25_score": 1.2,
        "hybrid_score": 0.9,
        "rank": 1,
    },
]


def test_kb_path_end_to_end():
    """正常知识库路径： TypedDict schema 下全链路跑通，_deps 通道保留"""
    store = StubStore(GOOD_RESULTS)
    deps = {
        "embedding_service": StubEmbedding(),
        "hybrid_store": store,
        "llm_client": StubLLM(),
    }
    graph = create_rag_graph(deps=deps)
    result = asyncio.run(graph.ainvoke(_initial_state()))

    assert result["answer"] == "stub answer"
    assert result["retrieval_confident"] is True
    assert len(result["filtered_results"]) == 1
    # _deps 是 schema 声明的私有通道，最终状态中应可见
    assert "embedding_service" in result["_deps"]
    # 检索有结果 → evidence_judge 不触发补检索
    assert store.calls == 1


def test_empty_retrieval_triggers_one_retry_loop():
    """检索为空 → evidence_judge 回边补检索恰好一轮 → generate 兜底"""
    store = StubStore([])
    deps = {
        "embedding_service": StubEmbedding(),
        "hybrid_store": store,
        "llm_client": StubLLM(),
    }
    graph = create_rag_graph(deps=deps)
    result = asyncio.run(graph.ainvoke(_initial_state()))

    # 首检 + 补检各一次（user_visible_scopes=None 不触发越权探测）
    assert store.calls == 2
    assert result["iteration_count"] == 1
    assert result["need_more_evidence"] is False
    assert result["answer"] == "stub answer"


def test_general_intent_skips_retrieval():
    """general 意图条件边短路：不向量化、不检索，直达 generate"""
    store = StubStore(GOOD_RESULTS)
    deps = {
        "query_rewriter": StubRewriterGeneral(),
        "embedding_service": StubEmbedding(),
        "hybrid_store": store,
        "llm_client": StubLLM(),
    }
    graph = create_rag_graph(deps=deps)
    result = asyncio.run(graph.ainvoke(_initial_state(question="你好")))

    assert store.calls == 0
    assert result["intent"] == "general"
    assert result["answer"] == "stub answer"
