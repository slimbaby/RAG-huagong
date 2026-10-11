"""
熔断器 - 防止级联故障
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional
import threading
import time
import logging

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    state_history: List[Dict] = field(default_factory=list)


class CircuitBreakerError(Exception):
    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


class CircuitBreaker:
    """线程安全的熔断器"""

    def __init__(self, name: str, failure_threshold: int = 5, recovery_timeout: int = 60, half_open_max_requests: int = 3, statistics_window: int = 300):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_requests = half_open_max_requests
        self.statistics_window = statistics_window
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = threading.Lock()
        self._half_open_requests = 0

    def _resolve_state(self) -> CircuitState:
        """锁内解析当前状态：OPEN 且恢复超时已到则惰性转 HALF_OPEN。
        调用方必须已持有 self._lock —— state property 与 can_execute 共用
        本方法，避免对不可重入的 threading.Lock 二次加锁导致死锁。"""
        if self._state == CircuitState.OPEN and self._stats.last_failure_time:
            if time.time() - self._stats.last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                self._half_open_requests = 0
                self._stats.state_history.append({"state": "half_open", "time": time.time()})
        return self._state

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._resolve_state()

    def can_execute(self) -> bool:
        with self._lock:
            current_state = self._resolve_state()
            if current_state == CircuitState.CLOSED:
                return True
            elif current_state == CircuitState.OPEN:
                return False
            else:
                # HALF_OPEN: 放行有限探测请求，并真实占用配额
                # （修复：此前 _half_open_requests 只清零从不递增，限流形同虚设）
                if self._half_open_requests < self.half_open_max_requests:
                    self._half_open_requests += 1
                    return True
                return False

    def record_success(self):
        with self._lock:
            self._stats.total_requests += 1
            self._stats.successful_requests += 1
            self._stats.consecutive_failures = 0
            self._stats.last_success_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._half_open_requests = 0
                self._stats.state_history.append({"state": "closed", "time": time.time()})

    def record_failure(self):
        with self._lock:
            self._stats.total_requests += 1
            self._stats.failed_requests += 1
            self._stats.consecutive_failures += 1
            self._stats.last_failure_time = time.time()
            if self._state == CircuitState.CLOSED and self._stats.consecutive_failures >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._stats.state_history.append({"state": "open", "time": time.time()})
            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._half_open_requests = 0
                self._stats.state_history.append({"state": "open", "time": time.time()})

    def get_stats(self) -> Dict:
        return {"name": self.name, "state": self.state.value, "total_requests": self._stats.total_requests, "successful_requests": self._stats.successful_requests, "failed_requests": self._stats.failed_requests, "consecutive_failures": self._stats.consecutive_failures, "last_failure_time": self._stats.last_failure_time, "last_success_time": self._stats.last_success_time, "state_history": self._stats.state_history[-10:]}

    def reset(self):
        with self._lock:
            self._state = CircuitState.CLOSED
            self._stats = CircuitStats()
            self._half_open_requests = 0

    def force_open(self):
        with self._lock:
            self._state = CircuitState.OPEN
            self._stats.state_history.append({"state": "open_forced", "time": time.time()})

    def force_close(self):
        with self._lock:
            self._state = CircuitState.CLOSED
            self._stats.state_history.append({"state": "closed_forced", "time": time.time()})


class CircuitBreakerManager:
    """熔断器管理器"""

    def __init__(self):
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get_breaker(self, name: str, **kwargs) -> CircuitBreaker:
        if name not in self._breakers:
            self._breakers[name] = CircuitBreaker(name=name, **kwargs)
        return self._breakers[name]

    def get_all_stats(self) -> Dict:
        return {name: breaker.get_stats() for name, breaker in self._breakers.items()}

    def reset_all(self):
        for breaker in self._breakers.values():
            breaker.reset()
