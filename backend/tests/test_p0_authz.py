"""P0 越权回归：评测端点管理员校验 + 流程实例 owner 校验。"""

import inspect

import pytest


def test_eval_endpoints_require_admin():
    """所有评测端点都必须挂 require_admin 依赖，
    否则任意登录用户可跑评测/改写评测集/读全部报告。"""
    from fastapi import Depends
    from app.api.auth import require_admin
    from app.api import evaluation as mod

    endpoints = [
        mod.run_evaluation, mod.get_eval_status, mod.get_eval_report,
        mod.list_eval_reports, mod.get_eval_dataset, mod.update_eval_dataset,
        mod.import_eval_dataset, mod.compare_eval_reports,
        mod.get_eval_dataset_stats, mod.get_stale_cases,
        mod.mark_stale_by_doc_id, mod.regenerate_eval_cases,
    ]
    for fn in endpoints:
        defaults = [p.default for p in inspect.signature(fn).parameters.values()]
        Dependency = type(Depends(require_admin))
        assert any(
            isinstance(d, Dependency) and getattr(d, "dependency", None) is require_admin
            for d in defaults
        ), f"{fn.__name__} 缺少 require_admin"


@pytest.mark.asyncio
async def test_workflow_resume_rejects_foreign_owner(monkeypatch):
    """非发起人无法凭 run_id 恢复他人流程（含替人审批下单）。"""
    from fastapi import HTTPException
    from app.api import workflows as mod
    from app.api.auth import CurrentUser

    class FakeGraph:
        async def aget_state(self, config):
            class S:
                values = {
                    "run_id": "r1",
                    "metadata": {"username": "alice"},
                }
            return S()

        async def ainvoke(self, *a, **kw):
            raise AssertionError("越权请求不应执行到 ainvoke")

    class FakeCandidate:
        id = "methane_order"
        version = 1

    class FakeRegistry:
        def list_flows(self):
            return [{"id": "methane_order", "version": 1}]

        def get(self, fid, ver):
            return FakeCandidate()

    class FakeEngine:
        def get_graph(self, *a, **kw):
            return FakeGraph()

    async def fake_cp():
        return object()

    monkeypatch.setattr(mod, "get_flow_registry", lambda: FakeRegistry())
    monkeypatch.setattr(mod, "get_workflow_engine", lambda: FakeEngine())
    monkeypatch.setattr(mod, "get_checkpointer", fake_cp)

    req = mod.ResumeWorkflowRequest(payload={"approved": True})
    bob = CurrentUser(username="bob", role_id="driver")

    with pytest.raises(HTTPException) as exc:
        await mod.resume_workflow("r1", req, bob)
    assert exc.value.status_code == 403


def test_app_debug_defaults_false():
    """APP_DEBUG 生产默认关闭，避免异常信息回显。"""
    from app.core.config import Settings
    assert Settings.model_fields["APP_DEBUG"].default is False
