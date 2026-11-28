"""P0 回归：SSE 流式问答在后台图异常 / 客户端断开时不得挂死。

修复前：_run_graph 抛异常 → 哨兵不写 → event_generator 永久阻塞在 queue.get()；
        客户端断开 → 后台 graph 继续跑（含下单/写记忆），异常被吞。
"""

import asyncio
import json

import pytest
from unittest.mock import patch

from app.api import chat as chat_mod
from app.api.auth import CurrentUser
from app.api.chat import ChatRequest, chat_query_stream


class ExplodingGraph:
    """跑一个节点后抛异常，异常里带内部敏感信息用于验证脱敏。"""

    async def astream(self, state):
        yield {"query_optimize": {"optimized_query": "x"}}
        raise RuntimeError("INTERNAL_SECRET_DSN_postgresql://user:pwd@host")


def _user():
    return CurrentUser(username="tester", role_id="admin")


@pytest.mark.asyncio
async def test_graph_exception_ends_stream_with_sanitized_error():
    """图异常必须转成 SSE error 事件并正常结束流，且不得回显内部异常文本。"""
    req = ChatRequest(question="测试", session_id="s1")

    with patch.object(chat_mod, "get_rag_graph", return_value=ExplodingGraph()), \
         patch.object(chat_mod, "get_deps", return_value={}), \
         patch.object(chat_mod, "_build_scope_context", return_value=(None, None)), \
         patch.object(chat_mod, "_resolve_flow", return_value={}), \
         patch.object(chat_mod, "_prepare_flow_request", return_value=None):
        resp = await chat_query_stream(req, _user())
        events = []
        async for chunk in resp.body_iterator:
            if isinstance(chunk, bytes):
                chunk = chunk.decode()
            events.append(json.loads(chunk.replace("data: ", "").strip()))

    kinds = [e["type"] for e in events]
    assert "error" in kinds, f"异常后未收到 error 事件: {kinds}"
    err = next(e for e in events if e["type"] == "error")
    assert "INTERNAL_SECRET" not in err["message"], "内部异常泄露给客户端"
    assert err["message"] == "服务处理失败，请稍后重试"


@pytest.mark.asyncio
async def test_client_disconnect_cancels_graph_task():
    """生成器被提前关闭（模拟客户端断开）时，后台 graph 任务必须被取消。"""
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class SlowGraph:
        async def astream(self, state):
            started.set()
            try:
                await asyncio.sleep(30)
                yield {"generate": {"answer": "should not finish"}}
            except asyncio.CancelledError:
                cancelled.set()
                raise

    req = ChatRequest(question="测试", session_id="s2")

    with patch.object(chat_mod, "get_rag_graph", return_value=SlowGraph()), \
         patch.object(chat_mod, "get_deps", return_value={}), \
         patch.object(chat_mod, "_build_scope_context", return_value=(None, None)), \
         patch.object(chat_mod, "_resolve_flow", return_value={}), \
         patch.object(chat_mod, "_prepare_flow_request", return_value=None):
        resp = await chat_query_stream(req, _user())
        iterator = resp.body_iterator.__aiter__()
        consume = asyncio.create_task(iterator.__anext__())
        await asyncio.wait_for(started.wait(), timeout=5)
        # 先取消正在等待的消费协程，再关闭生成器，否则 aclose 报 "already running"
        consume.cancel()
        try:
            await consume
        except (asyncio.CancelledError, StopAsyncIteration):
            pass
        await iterator.aclose()
        await asyncio.wait_for(cancelled.wait(), timeout=5)

    assert cancelled.is_set(), "客户端断开后后台 graph 未被取消"
