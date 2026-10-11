"""韧性执行器 - 熔断 + 重试的统一入口（2026-09-25 接线）

此前 resilience 层只有组件没有执行器：RetryConfig 是纯配置声明，
熔断器全仓库无调用点，api/resilience.py 是空桩。本模块补上缺口，
作为 LLM 调用 / Milvus 检索 / BM25 检索接入韧性保护的统一入口。

设计原则：
- 正常路径零侵入：熔断器 CLOSED 且调用成功时，额外开销只有两次加锁状态读写；
- 熔断 OPEN 时快速失败抛 CircuitBreakerError，调用点（nodes.py 各节点、
  hybrid_store 检索）已有的 try/except 降级路径天然接住，不会新增崩溃面；
- 只对瞬态异常（超时/连接/限流/5xx）重试与计入熔断；业务异常
  （参数错误、解析失败等）立即抛出且不触发熔断——它们不代表依赖不健康；
- 同步路径（to_thread 内的 pymilvus/jieba 调用）无法安全中断线程，
  因此只做熔断不做超时；LLM 的超时由 openai SDK 的 60s 显式配置承担。
"""

import asyncio
import logging
import random
import time
from typing import Any, Callable, Coroutine, Optional, Tuple

from .circuit_breaker import CircuitBreakerError
from .resilience_manager import ResilienceManager
from ..core.resilience_config import ResilienceConfig, resilience_config

logger = logging.getLogger(__name__)


def _matches_retryable_names(exc: BaseException, names) -> bool:
    """按 MRO 类名匹配配置里的 retryable_exceptions 字符串列表
    （如 "TimeoutError"/"ConnectionError"/"RateLimitError" 命中内置异常
    与 openai.RateLimitError 等）"""
    return any(cls.__name__ in names for cls in type(exc).__mro__)


class ResilienceExecutor:
    """熔断 + 重试执行器。全局单例（get_executor），保证 api/resilience.py
    看到的状态与主流程实际使用的是同一批熔断器实例。"""

    def __init__(self, config: ResilienceConfig):
        self.config = config
        self.manager = ResilienceManager(config)

    # ---------- 内部工具 ----------

    def _breaker(self, name: str):
        return self.manager.get_circuit_breaker(name)

    def _is_transient(self, exc: Exception, retryable: Optional[Tuple]) -> bool:
        if retryable and isinstance(exc, retryable):
            return True
        return _matches_retryable_names(exc, self.config.retry.retryable_exceptions)

    def _backoff_delay(self, attempt: int) -> float:
        """第 attempt 次失败后的退避时长（attempt 从 1 起）"""
        r = self.config.retry
        if r.delay_strategy == "fixed":
            delay = r.base_delay
        elif r.delay_strategy == "linear":
            delay = r.base_delay * attempt
        else:  # exponential（默认）
            delay = r.base_delay * (r.backoff_factor ** (attempt - 1))
        delay = min(delay, r.max_delay)
        if r.jitter:
            delay *= 0.5 + random.random()  # 0.5~1.5 倍抖动，避免重试风暴同步化
        return delay

    # ---------- 对外入口 ----------

    async def execute_async(
        self,
        name: str,
        coro_factory: Callable[[], Coroutine],
        *,
        max_attempts: int = 1,
        retryable: Optional[Tuple] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """包裹异步调用：熔断检查 → 执行 → 瞬态失败按指数退避重试。

        name:         熔断器名（如 "llm" / "milvus" / "bm25"）
        coro_factory: 无参函数，每次调用返回全新 coroutine（重试必须可重放）
        max_attempts: 总尝试次数（含首次）；1 = 只熔断不重试
        retryable:    额外视为瞬态的异常类型元组（如 openai 的限流/超时类）
        timeout:      可选 asyncio.wait_for 超时秒数
        """
        breaker = self._breaker(name)
        attempts = max(1, max_attempts)
        for attempt in range(1, attempts + 1):
            if self.config.circuit_breaker.enabled and not breaker.can_execute():
                raise CircuitBreakerError(
                    f"熔断器 '{name}' 已打开，快速失败（{breaker.recovery_timeout}s 后半开试探）",
                    retry_after=breaker.recovery_timeout,
                )
            try:
                coro = coro_factory()
                result = await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
                if self.config.circuit_breaker.enabled:
                    breaker.record_success()
                return result
            except asyncio.CancelledError:
                raise  # 任务取消不是依赖故障，不计入熔断也不重试
            except Exception as e:
                transient = self._is_transient(e, retryable)
                if transient and self.config.circuit_breaker.enabled:
                    breaker.record_failure()
                if transient and attempt < attempts:
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        f"[resilience:{name}] 瞬态失败(第{attempt}/{attempts}次)，{delay:.1f}s 后重试: {e}"
                    )
                    await asyncio.sleep(delay)
                    continue
                raise

    def execute_sync(
        self,
        name: str,
        fn: Callable[[], Any],
        *,
        max_attempts: int = 1,
        retryable: Optional[Tuple] = None,
    ) -> Any:
        """同步版（供 to_thread 内的 pymilvus/jieba 调用）。
        同步路径无法安全中断，不提供 timeout；默认只熔断不重试。"""
        breaker = self._breaker(name)
        attempts = max(1, max_attempts)
        for attempt in range(1, attempts + 1):
            if self.config.circuit_breaker.enabled and not breaker.can_execute():
                raise CircuitBreakerError(
                    f"熔断器 '{name}' 已打开，快速失败（{breaker.recovery_timeout}s 后半开试探）",
                    retry_after=breaker.recovery_timeout,
                )
            try:
                result = fn()
                if self.config.circuit_breaker.enabled:
                    breaker.record_success()
                return result
            except Exception as e:
                transient = self._is_transient(e, retryable)
                if transient and self.config.circuit_breaker.enabled:
                    breaker.record_failure()
                if transient and attempt < attempts:
                    delay = self._backoff_delay(attempt)
                    logger.warning(
                        f"[resilience:{name}] 瞬态失败(第{attempt}/{attempts}次)，{delay:.1f}s 后重试: {e}"
                    )
                    time.sleep(delay)
                    continue
                raise

    def apply_config(self, config: ResilienceConfig):
        """进程内动态更新配置（不持久化）。注意：已创建的熔断器实例
        保留原有阈值参数，新参数对之后创建的熔断器生效。"""
        self.config = config
        self.manager.config = config


# ---------- 全局单例 ----------

_executor: Optional[ResilienceExecutor] = None


def get_executor() -> ResilienceExecutor:
    """进程级单例：llm_service / hybrid_store / api.resilience 共用，
    保证熔断状态与统计的一致性"""
    global _executor
    if _executor is None:
        _executor = ResilienceExecutor(resilience_config)
    return _executor


def get_manager() -> ResilienceManager:
    return get_executor().manager
