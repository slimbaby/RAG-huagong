"""P0 缺陷修复的回归测试（2026-09-25）

覆盖三项修复：
1. Milvus 删除/查询表达式双重引号（hybrid_store / memory_manager）
   —— 非法表达式 + 异常被吞曾导致删除静默失败
2. 熔断器 can_execute 对不可重入 Lock 二次加锁必然死锁
   —— 顺带验证 half-open 探测配额真实生效
3. 幽灵用户名（JWT 验签通过但 users 表查无此人）回落 admin 的越权通道
   —— 改为服务账号显式白名单 + 未知用户 403
"""

import asyncio
import threading

import pytest


# ============================================================
# P0-2: 熔断器死锁 + half-open 配额
# ============================================================

def test_can_execute_no_deadlock():
    """旧实现 can_execute 持锁再调 state property 二次加锁 → 必然死锁。
    放到线程里带超时跑：死锁则 join 超时，测试失败而不是挂死整个 pytest。"""
    from app.resilience.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(
        name="t", failure_threshold=1, recovery_timeout=0, half_open_max_requests=2
    )
    results = {}

    def worker():
        results["closed"] = cb.can_execute()
        cb.record_failure()  # threshold=1 → OPEN；recovery=0 → 惰性转 HALF_OPEN
        results["probe1"] = cb.can_execute()
        results["probe2"] = cb.can_execute()
        results["probe3"] = cb.can_execute()

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=3)
    assert not t.is_alive(), "can_execute 死锁（不可重入 Lock 二次加锁）"
    assert results["closed"] is True
    # half-open：配额 2，第三次拒绝（旧实现只清零从不递增，会无限放行）
    assert results["probe1"] is True
    assert results["probe2"] is True
    assert results["probe3"] is False


def test_half_open_success_closes_and_resets_quota():
    from app.resilience.circuit_breaker import CircuitBreaker, CircuitState

    cb = CircuitBreaker(
        name="t", failure_threshold=1, recovery_timeout=0, half_open_max_requests=1
    )
    cb.record_failure()
    assert cb.can_execute() is True   # 唯一探测配额
    assert cb.can_execute() is False  # 配额用尽
    cb.record_success()               # 探测成功 → CLOSED 且配额清零
    assert cb.state == CircuitState.CLOSED
    assert cb.can_execute() is True
    assert cb.can_execute() is True   # CLOSED 不限流


def test_half_open_failure_reopens():
    import time

    from app.resilience.circuit_breaker import CircuitBreaker, CircuitState

    cb = CircuitBreaker(
        name="t", failure_threshold=1, recovery_timeout=50, half_open_max_requests=2
    )
    cb.record_failure()
    assert cb.state == CircuitState.OPEN
    # 模拟恢复超时已过 → 惰性转 HALF_OPEN
    cb._stats.last_failure_time = time.time() - 60
    assert cb.can_execute() is True   # half-open 探测
    cb.record_failure()               # 探测失败 → 重新 OPEN（last_failure 刚刷新）
    assert cb.state == CircuitState.OPEN


# ============================================================
# P0-1: Milvus 表达式拼接
# ============================================================

def test_build_eq_expr_single_quoting():
    """回归防护：_milvus_escape 返回值已含引号，
    不得再出现 f'f == "{_milvus_escape(v)}"' 的双重引号写法。"""
    from app.vectorstore.hybrid_store import _build_eq_expr, _milvus_escape

    expr = _build_eq_expr("doc_id", "abc123")
    assert expr == 'doc_id == "abc123"'
    assert '== ""' not in expr

    # 含引号/反斜杠的恶意文件名：转义正确且仍只有一对外层引号
    evil = 'a"b\\c.pdf'
    expr = _build_eq_expr("source_file", evil)
    assert expr == f"source_file == {_milvus_escape(evil)}"
    assert expr.count('"') == 3  # 外层一对 + 被转义的内嵌引号（已带 \）
    assert '\\"' in expr and '\\\\' in expr


def test_delete_by_doc_id_raises_on_failure():
    """删除失败必须向上抛：吞异常会让 documents.py
    「删除失败中止重索引」的保护失效，产生孤儿 chunk。"""
    from app.vectorstore.hybrid_store import HybridMilvusStore

    class BrokenCollection:
        def delete(self, expr):
            # 模拟 pymilvus 对非法表达式/连接故障抛错
            raise RuntimeError("milvus delete failed")

        def flush(self):
            pass

    store = HybridMilvusStore.__new__(HybridMilvusStore)  # 绕过 __init__ 的外部依赖
    store._connected = True
    store.collection = BrokenCollection()
    store.bm25 = None  # 删除抛错后不会走到 BM25 重建

    with pytest.raises(RuntimeError):
        asyncio.run(store.delete_by_doc_id("doc1"))


# ============================================================
# P0-3: 幽灵用户名越权通道
# ============================================================

def test_service_account_explicit_grant():
    """eval_runner 走 SERVICE_ACCOUNTS 显式白名单，不依赖查库回落。"""
    from app.services.permission_service import get_user_role

    role = asyncio.run(get_user_role("eval_runner"))
    assert role == {"role_id": "service", "role_name": "服务账号", "scope_ids": ["all"]}


def test_ghost_user_rejected_403(monkeypatch):
    """JWT 验签通过但 users 表查无此人 → 403，不再回落 admin。"""
    import app.services.permission_service as ps
    from fastapi import HTTPException

    async def fake_get_user_role(username):
        return None  # 模拟 users 表查无此人

    monkeypatch.setattr(ps, "get_user_role", fake_get_user_role)
    from app.api.auth import get_current_user

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_current_user(username="ghost_user"))
    assert exc_info.value.status_code == 403


def test_ghost_user_visible_scopes_empty_not_admin(monkeypatch):
    """get_visible_scopes 对未知用户返回 []（无权限），
    而不是 None——None 是 admin 免过滤的特权语义。"""
    import app.services.permission_service as ps

    async def fake_get_user_role(username):
        return None

    monkeypatch.setattr(ps, "get_user_role", fake_get_user_role)
    visible = asyncio.run(ps.get_visible_scopes("ghost_user"))
    assert visible == []


def test_auth_disabled_passthrough(monkeypatch):
    """AUTH_ENABLED=False 是部署方主动关闭认证的显式开关，保持放行。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "AUTH_ENABLED", False)
    from app.api.auth import get_current_user

    user = asyncio.run(get_current_user(username="anonymous"))
    assert user.role_id == "admin"
    assert user.scope_ids == ["all"]


def test_current_user_defaults_least_privilege():
    """CurrentUser 默认值必须是最小权限，防任何路径意外回落 admin。"""
    from app.api.auth import CurrentUser

    u = CurrentUser(username="x")
    assert u.role_id == "unknown"
    assert u.scope_ids == []
