"""
韧性管理器 - 统一管理所有韧性组件
"""

from typing import Dict, Optional
from .circuit_breaker import CircuitBreakerManager
from .timeout_controller import TimeoutController, IterationController
from .error_logger import ErrorLogger
from ..core.resilience_config import ResilienceConfig


class ResilienceManager:
    """韧性管理器"""

    def __init__(self, config: ResilienceConfig):
        self.config = config
        self.timeout_controller = TimeoutController(config.timeouts)
        self.circuit_breaker_manager = CircuitBreakerManager()
        self.error_logger = ErrorLogger(config.error_logging.storage_path)
        self._iteration_controllers: Dict[str, IterationController] = {}

    def get_iteration_controller(self, name: str, max_iterations: int = 10) -> IterationController:
        if name not in self._iteration_controllers:
            self._iteration_controllers[name] = IterationController(max_iterations=max_iterations, name=name)
        return self._iteration_controllers[name]

    def get_circuit_breaker(self, name: str):
        return self.circuit_breaker_manager.get_breaker(
            name=name,
            failure_threshold=self.config.circuit_breaker.failure_threshold,
            recovery_timeout=self.config.circuit_breaker.recovery_timeout,
            half_open_max_requests=self.config.circuit_breaker.half_open_max_requests,
        )

    def get_all_status(self) -> Dict:
        return {
            "circuit_breakers": self.circuit_breaker_manager.get_all_stats(),
            "iteration_controllers": {name: ctrl.get_status() for name, ctrl in self._iteration_controllers.items()},
            "error_statistics": self.error_logger.get_error_statistics(),
        }

    def reset_all(self):
        self.circuit_breaker_manager.reset_all()
        for ctrl in self._iteration_controllers.values():
            ctrl.reset()
