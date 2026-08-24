"""
超时和迭代控制器
"""

import asyncio
from dataclasses import dataclass
from typing import Optional, List, Dict
from contextlib import asynccontextmanager
import time
import logging

logger = logging.getLogger(__name__)


class TimeoutError(Exception):
    def __init__(self, message: str, timeout_seconds: float = 0, elapsed_seconds: float = 0):
        super().__init__(message)
        self.timeout_seconds = timeout_seconds
        self.elapsed_seconds = elapsed_seconds


class IterationLimitError(Exception):
    def __init__(self, message: str, max_iterations: int = 0, current_iteration: int = 0):
        super().__init__(message)
        self.max_iterations = max_iterations
        self.current_iteration = current_iteration


@dataclass
class ExecutionMetrics:
    start_time: float = 0
    end_time: float = 0
    duration: float = 0
    iterations: int = 0
    timed_out: bool = False
    iteration_limit_reached: bool = False


class TimeoutController:
    """超时控制器"""

    def __init__(self, config=None):
        self.config = config

    @asynccontextmanager
    async def timeout(self, seconds: int, operation_name: str = ""):
        start = time.time()
        try:
            yield
        finally:
            elapsed = time.time() - start
            if elapsed > seconds:
                raise TimeoutError(f"Operation {operation_name} timed out after {elapsed:.1f}s (limit: {seconds}s)", timeout_seconds=seconds, elapsed_seconds=elapsed)

    async def execute_with_timeout(self, coro, timeout_seconds: int, operation_name: str = ""):
        try:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Operation {operation_name} timed out after {timeout_seconds}s", timeout_seconds=timeout_seconds)

    def get_timeout(self, operation_type: str) -> int:
        if self.config:
            return getattr(self.config, operation_type, 30)
        return 30


class IterationController:
    """迭代控制器"""

    def __init__(self, max_iterations: int = 10, name: str = "default"):
        self.max_iterations = max_iterations
        self.name = name
        self.current_iteration = 0
        self.history: List[Dict] = []

    def increment(self):
        self.current_iteration += 1
        if self.current_iteration > self.max_iterations:
            raise IterationLimitError(f"Iteration limit exceeded for {self.name}: {self.current_iteration}/{self.max_iterations}", max_iterations=self.max_iterations, current_iteration=self.current_iteration)
        self.history.append({"iteration": self.current_iteration, "time": time.time()})

    def reset(self):
        self.current_iteration = 0
        self.history = []

    def check(self):
        if self.current_iteration >= self.max_iterations:
            raise IterationLimitError(f"Iteration limit reached for {self.name}", max_iterations=self.max_iterations, current_iteration=self.current_iteration)

    def get_status(self) -> Dict:
        return {"name": self.name, "current_iteration": self.current_iteration, "max_iterations": self.max_iterations, "remaining": self.max_iterations - self.current_iteration}
