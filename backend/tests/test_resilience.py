"""Test resilience module."""

import pytest


def test_circuit_breaker():
    from app.resilience.circuit_breaker import CircuitBreaker, CircuitState

    cb = CircuitBreaker(
        name="test",
        failure_threshold=3,
        recovery_timeout=5
    )
    assert cb.state == CircuitState.CLOSED
    stats = cb.get_stats()
    assert stats["consecutive_failures"] == 0


def test_circuit_breaker_open():
    from app.resilience.circuit_breaker import CircuitBreaker, CircuitState

    cb = CircuitBreaker(
        name="test",
        failure_threshold=2,
        recovery_timeout=5
    )
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN


def test_circuit_breaker_close_on_success():
    from app.resilience.circuit_breaker import CircuitBreaker, CircuitState

    cb = CircuitBreaker(
        name="test",
        failure_threshold=3,
        recovery_timeout=5
    )
    cb.record_failure()
    cb.record_success()
    stats = cb.get_stats()
    assert stats["consecutive_failures"] == 0
    assert cb.state == CircuitState.CLOSED


def test_timeout_controller():
    from app.resilience.timeout_controller import TimeoutController

    tc = TimeoutController()
    assert tc is not None


def test_resilience_config():
    from app.core.resilience_config import ResilienceConfig
    config = ResilienceConfig()
    # Should have default values
    assert config.circuit_breaker is not None
    assert config.timeouts is not None
