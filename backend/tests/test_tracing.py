"""Tracer 行为测试：span 嵌套、asyncio 并发隔离、异常标记、上下文回滚。

这些是异步场景下的易回归点（早期用 threading.local 会导致并发请求互相串 trace），
用同步 asyncio.run 包装，只依赖 pytest 本身，不需要 pytest-asyncio。
"""

import asyncio
import json

import pytest

from app.tracing.tracer import Tracer, SpanType


def _run(coro):
    return asyncio.run(coro)


async def _nested_request(tracer, name, delay=0.01):
    """模拟一次 chat_query：root span 下挂 query_rewrite / retrieval，retrieval 下再挂子 span"""
    trace_id = tracer.start_trace(user_query=name)
    with tracer.start_span(SpanType.REQUEST, "chat_query", {"question": name}):
        await asyncio.sleep(delay)
        with tracer.start_span(SpanType.LLM_CALL, "query_rewrite"):
            await asyncio.sleep(0.02)
        with tracer.start_span(SpanType.RETRIEVAL, "retrieval"):
            with tracer.start_span(SpanType.VECTOR_SEARCH, "vector_search"):
                await asyncio.sleep(0.01)
            with tracer.start_span(SpanType.BM25_SEARCH, "bm25_search"):
                await asyncio.sleep(0.01)
        tracer.record_tokens(10, 5)
    tracer.end_trace(trace_id=trace_id, answer="ok", sources_count=3)
    return trace_id


def _read_detail(tracer, trace_id):
    return json.load(open(tracer.storage_path + f"/details/{trace_id}.json", encoding="utf-8"))


def test_spans_form_a_tree(tmp_path):
    tracer = Tracer(storage_path=str(tmp_path))

    trace_id = _run(_nested_request(tracer, "q0"))
    tracer._shutdown_executor()

    detail = _read_detail(tracer, trace_id)
    spans = detail["spans"]
    assert len(spans) == 5

    by_id = {s["span_id"]: s for s in spans}
    roots = [s for s in spans if s["parent_span_id"] is None]
    assert len(roots) == 1, "应只有一个 root span"
    assert roots[0]["depth"] == 0

    for s in spans:
        if s["parent_span_id"] is None:
            continue
        parent = by_id[s["parent_span_id"]]
        assert parent["depth"] == s["depth"] - 1
        assert s["start_time"] >= parent["start_time"]
        assert s["end_time"] <= parent["end_time"] + 1e-6


def test_concurrent_requests_do_not_leak_context(tmp_path):
    """并发下每个请求的 span 必须归属自己的 trace，且结束后上下文无残留"""
    tracer = Tracer(storage_path=str(tmp_path))

    async def _all():
        return await asyncio.gather(*[_nested_request(tracer, f"q{i}", 0.05 - i * 0.01) for i in range(5)])

    trace_ids = _run(_all())
    tracer._shutdown_executor()

    assert len(set(trace_ids)) == 5
    for tid, name in zip(trace_ids, [f"q{i}" for i in range(5)]):
        detail = _read_detail(tracer, tid)
        assert detail["user_query"] == name
        assert len(detail["spans"]) == 5, "span 不能被并发请求抢走"
        assert all(s["trace_id"] == tid for s in detail["spans"])
        assert detail["tokens_used"]["total"] == 15, "token 必须记到自己的 trace"

    from app.tracing import tracer as tracer_module
    assert tracer_module._trace_id_var.get() == ""
    assert tracer_module._span_stack_var.get() == ()
    assert tracer._ctx_tokens == {}


def test_error_marks_span_and_reraises(tmp_path):
    tracer = Tracer(storage_path=str(tmp_path))

    async def boom():
        trace_id = tracer.start_trace(user_query="boom")
        try:
            with tracer.start_span(SpanType.REQUEST, "root"):
                with tracer.start_span(SpanType.LLM_CALL, "will_fail"):
                    raise ValueError("inner boom")
        except ValueError:
            pass
        tracer.end_trace(trace_id=trace_id, status="error", error_message="inner boom")
        return trace_id

    trace_id = _run(boom())
    tracer._shutdown_executor()

    detail = _read_detail(tracer, trace_id)
    failed = {s["operation_name"] for s in detail["spans"] if s["status"] == "error"}
    assert failed == {"will_fail", "root"}, "子 span 失败应冒泡标记到父 span"
    assert detail["status"] == "error"

    from app.tracing import tracer as tracer_module
    assert tracer_module._span_stack_var.get() == (), "异常后 span 栈必须回滚"


def test_persist_does_not_block_and_writes_both_files(tmp_path):
    tracer = Tracer(storage_path=str(tmp_path))

    trace_id = _run(_nested_request(tracer, "q0"))
    tracer._shutdown_executor()

    assert (tmp_path / "details" / f"{trace_id}.json").exists()
    jsonl_files = list(tmp_path.glob("traces_*.jsonl"))
    assert len(jsonl_files) == 1
    rows = [json.loads(line) for line in jsonl_files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 1
    assert "spans" not in rows[0], "摘要行不应包含 spans"
    assert rows[0]["trace_id"] == trace_id


def test_end_trace_after_executor_shutdown_still_persists(tmp_path):
    """进程退出中 executor 已关闭时，降级为同步落盘，不丢数据"""
    tracer = Tracer(storage_path=str(tmp_path))
    trace_id = tracer.start_trace(user_query="late")
    tracer._shutdown_executor()
    tracer.end_trace(trace_id=trace_id, answer="late answer")

    assert (tmp_path / "details" / f"{trace_id}.json").exists()
