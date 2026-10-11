"""韧性执行器接线测试（2026-09-25）。

覆盖：
- execute_async/execute_sync 的熔断 + 瞬态重试 + 非瞬态直通语义
- llm_service._create_completion 走执行器（重试后成功 / 熔断快速失败）
- api/resilience.py 从空桩变为真实状态
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from app.core.resilience_config import (
    CircuitBreakerConfig,
    ResilienceConfig,
    RetryConfig,
)
from app.resilience.circuit_breaker import CircuitBreakerError, CircuitState
from app.resilience.executor import ResilienceExecutor


def _fast_config(failure_threshold=2):
    return ResilienceConfig(
        circuit_breaker=CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            recovery_timeout=60,
            half_open_max_requests=1,
        ),
        retry=RetryConfig(base_delay=0.001, jitter=False),
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """退避 sleep 归零，测试不等待"""
    async def instant(_delay):
        return None
    monkeypatch.setattr(asyncio, "sleep", instant)


# ============================================================
# 执行器语义
# ============================================================

def test_execute_async_success_passthrough():
    ex = ResilienceExecutor(_fast_config())

    async def ok():
        return 42

    result = asyncio.run(ex.execute_async("t", ok))
    assert result == 42
    stats = ex.manager.circuit_breaker_manager.get_all_stats()
    assert stats["t"]["successful_requests"] == 1


def test_execute_async_retries_transient_then_succeeds():
    ex = ResilienceExecutor(_fast_config(failure_threshold=5))
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TimeoutError("transient")
        return "recovered"

    result = asyncio.run(ex.execute_async("t", flaky, max_attempts=3))
    assert result == "recovered"
    assert calls["n"] == 3


def test_execute_async_non_transient_no_retry_no_breaker():
    """业务异常（非瞬态）：立即抛出、不重试、不计入熔断"""
    ex = ResilienceExecutor(_fast_config(failure_threshold=1))
    calls = {"n": 0}

    async def bad():
        calls["n"] += 1
        raise ValueError("business error")

    with pytest.raises(ValueError):
        asyncio.run(ex.execute_async("t", bad, max_attempts=3))
    assert calls["n"] == 1
    stats = ex.manager.circuit_breaker_manager.get_all_stats()
    assert stats["t"]["failed_requests"] == 0
    assert stats["t"]["state"] == "closed"


def test_execute_async_breaker_opens_and_fast_fails():
    ex = ResilienceExecutor(_fast_config(failure_threshold=2))
    calls = {"n": 0}

    async def down():
        calls["n"] += 1
        raise ConnectionError("service down")

    # 前两次真实调用失败 → 熔断 OPEN
    for _ in range(2):
        with pytest.raises(ConnectionError):
            asyncio.run(ex.execute_async("t", down))
    # 第三次：熔断快速失败，不再触碰底层
    with pytest.raises(CircuitBreakerError):
        asyncio.run(ex.execute_async("t", down))
    assert calls["n"] == 2


def test_execute_sync_breaker_only():
    ex = ResilienceExecutor(_fast_config(failure_threshold=1))

    def down():
        raise ConnectionError("milvus down")

    with pytest.raises(ConnectionError):
        ex.execute_sync("milvus", down)
    with pytest.raises(CircuitBreakerError):
        ex.execute_sync("milvus", down)  # OPEN 快速失败


# ============================================================
# llm_service 接线
# ============================================================

def _make_llm_service():
    from app.services.llm_service import LLMService
    svc = LLMService(api_key="test", base_url="http://localhost:9999/v1")
    return svc


def _fake_response(text="ok"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    resp.usage.prompt_tokens = 1
    resp.usage.completion_tokens = 2
    resp.usage.total_tokens = 3
    return resp


def test_llm_service_retries_transient(monkeypatch):
    """create 第一次瞬态失败、第二次成功 → generate 返回正常结果"""
    import app.resilience.executor as executor_mod
    monkeypatch.setattr(executor_mod, "_executor", ResilienceExecutor(_fast_config(failure_threshold=5)))

    svc = _make_llm_service()
    create = svc.client.chat.completions.create
    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("llm transient")
        return _fake_response("答案")

    svc.client.chat.completions.create = flaky

    result = asyncio.run(svc.generate("问题"))
    assert result["text"] == "答案"
    assert calls["n"] == 2


def test_llm_service_breaker_fail_fast(monkeypatch):
    """连续瞬态失败触发熔断后，后续调用不再触碰 LLM 客户端"""
    import app.resilience.executor as executor_mod
    monkeypatch.setattr(executor_mod, "_executor", ResilienceExecutor(_fast_config(failure_threshold=2)))

    svc = _make_llm_service()
    calls = {"n": 0}

    async def down(**kwargs):
        calls["n"] += 1
        raise TimeoutError("llm down")

    svc.client.chat.completions.create = down

    # llm_max_retries 默认 3 → 单次 generate 内尝试：第1、2次真实失败
    # （达到阈值 2 → OPEN），第 3 次尝试被熔断拦截 → 抛 CircuitBreakerError
    with pytest.raises(CircuitBreakerError):
        asyncio.run(svc.generate("问题"))
    assert calls["n"] == 2
    # 熔断 OPEN：再次调用快速失败，底层不再被触碰
    with pytest.raises(CircuitBreakerError):
        asyncio.run(svc.generate("问题"))
    assert calls["n"] == 2


# ============================================================
# api/resilience 真实状态
# ============================================================

def test_resilience_api_returns_real_stats(monkeypatch):
    import app.resilience.executor as executor_mod
    ex = ResilienceExecutor(_fast_config())
    monkeypatch.setattr(executor_mod, "_executor", ex)

    from app.api import resilience as api

    async def trigger():
        raise ConnectionError("x")

    with pytest.raises(ConnectionError):
        asyncio.run(ex.execute_async("llm", trigger))

    status = asyncio.run(api.get_resilience_status())
    assert "llm" in status["circuit_breakers"]
    assert status["circuit_breakers"]["llm"]["failed_requests"] == 1

    breakers = asyncio.run(api.get_circuit_breakers())
    assert breakers["llm"]["state"] == "closed"

    reset_resp = asyncio.run(api.reset_circuit_breaker("llm"))
    assert reset_resp["status"] == "reset"

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        asyncio.run(api.reset_circuit_breaker("nonexistent"))

    cfg = asyncio.run(api.get_resilience_config())
    assert cfg["circuit_breaker"]["failure_threshold"] == 2
