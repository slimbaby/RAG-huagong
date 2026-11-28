"""P1/P2 必要性评审后保留的高影响修复回归测试。"""

import threading

import pytest


@pytest.mark.asyncio
async def test_reranker_splits_long_doc_and_takes_max_score(monkeypatch):
    from app.services import reranker_service as rs

    class FakeTokenizer:
        def encode(self, text, add_special_tokens=False):
            return [ord(ch) for ch in text]

        def decode(self, ids, skip_special_tokens=False):
            return "".join(chr(i) for i in ids)

    captured = {}

    class FakeModel:
        tokenizer = FakeTokenizer()

        def predict(self, pairs, **kwargs):
            captured["pairs"] = pairs
            return [0.1, 0.9, 0.2, 0.3]

    monkeypatch.setattr(rs, "_model_instance", FakeModel())
    service = rs.RerankerService()
    scores = await service.rerank("问题", ["甲" * 1000, "短文档"])

    assert len(captured["pairs"]) == 4  # 长文 3 窗 + 短文 1 窗
    assert scores == [0.9, 0.3]  # 长文取窗口最高分，短文档用自身得分


def test_law_date_prompts_forbid_fabrication():
    from app.prompts.memory import SYSTEM_PROMPT_WITH_MEMORY
    from app.prompts.rag import (
        COMPARISON_PROMPT,
        RAG_PROMPT_WITH_CITATION_AND_TIME,
    )

    for prompt in (RAG_PROMPT_WITH_CITATION_AND_TIME, COMPARISON_PROMPT, SYSTEM_PROMPT_WITH_MEMORY):
        assert "不得推测或编造" in prompt


@pytest.mark.asyncio
async def test_memory_recall_keeps_score_paired_after_filtering(monkeypatch):
    from app.services.memory_manager import Memory, MemoryManager

    class FakeEmbedding:
        async def embed_query(self, text):
            return [0.0]

    manager = MemoryManager(None, None, FakeEmbedding(), None, milvus_store=object())
    manager._memories = {
        "expired": Memory("expired", "s", "过期", "fact", 1, expired=True),
        "low": Memory("low", "s", "低相关", "fact", 1),
        "high": Memory("high", "s", "高相关", "fact", 1),
    }

    async def fake_search(*args, **kwargs):
        return [
            {"memory_id": "expired", "score": 0.99},
            {"memory_id": "low", "score": 0.1},
            {"memory_id": "high", "score": 0.9},
        ]

    monkeypatch.setattr(manager, "_search_similar_memories", fake_search)
    monkeypatch.setattr(manager, "_persist_memories", lambda: None)
    ranked = await manager.recall("查询", session_id="s")
    assert [m.memory_id for m in ranked] == ["high", "low"]


@pytest.mark.asyncio
async def test_empty_results_after_retry_are_not_confident():
    from app.agents.nodes import check_retrieval_confidence_node

    result = await check_retrieval_confidence_node(
        {"filtered_results": [], "iteration_count": 2}
    )
    assert result == {"retrieval_confident": False}


@pytest.mark.asyncio
async def test_eval_session_skips_lesson_injection():
    from app.agents.nodes import generate_node

    class FakeLessonStore:
        def __init__(self):
            self.calls = 0

        def build_lesson_text(self, session_id=None):
            self.calls += 1
            return "不该出现的教训"

    class FakeLLM:
        async def generate(self, prompt):
            assert "不该出现的教训" not in prompt
            return {"text": "ok"}

    lessons = FakeLessonStore()
    result = await generate_node({
        "user_query": "问题",
        "optimized_query": "问题",
        "session_id": "eval_case_20260101",
        "intent": "general",
        "filtered_results": [],
        "retrieval_confident": False,
        "_deps": {"llm_client": FakeLLM(), "lesson_store": lessons},
    })
    assert result["answer"] == "ok"
    assert lessons.calls == 0


@pytest.mark.asyncio
async def test_sync_tool_does_not_block_event_loop():
    from app.agents.nodes import _chat_with_tools

    threads = []

    def probe():
        threads.append(threading.current_thread())
        return "ok"

    class FakeLLM:
        def __init__(self):
            self.round = 0

        async def chat_with_tools(self, messages, tools=None):
            self.round += 1
            if self.round == 1:
                return {"text": "", "tool_calls": [
                    {"id": "1", "function": {"name": "probe", "arguments": "{}"}}
                ]}
            return {"text": "done"}

    await _chat_with_tools(FakeLLM(), [], [], {"probe": probe})
    assert threads and threads[0] is not threading.current_thread()


@pytest.mark.asyncio
async def test_session_store_timeouts_backoff_and_memory_ttl(monkeypatch):
    from app.services.session_store import SessionStore

    created = []

    class FakeRedis:
        def __init__(self, **kwargs):
            created.append(kwargs)

        async def ping(self):
            raise RuntimeError("redis down")

    import redis.asyncio as aioredis

    monkeypatch.setattr(aioredis, "Redis", FakeRedis)

    store = SessionStore("redis://localhost:6379/0", ttl=1)
    await store.connect()
    assert created[0]["socket_timeout"] == 2.0
    assert created[0]["socket_connect_timeout"] == 2.0
    assert store._connected is False

    await store.connect()  # 退避窗口内不应重复尝试连接
    assert len(created) == 1

    await store.add_message("s1", "user", "内容")
    assert len(await store.get_messages("s1")) == 1
    store._session_meta_mem["s1"]["last_active"] -= 10
    assert await store.get_messages("s1") == []


def test_trace_status_flags_degraded_answers():
    from app.api.chat import _trace_status

    assert _trace_status({"error": "x"}, "正常回答") == "error"
    assert _trace_status({}, "生成回答时出现错误: boom") == "error"
    assert _trace_status({}, "正常回答") == "ok"
