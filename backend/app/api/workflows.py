"""
Workflow API

独立于 /chat/query 的有状态流程接口。
/chat/query 是一次性问答（无状态）；workflow 是跨请求长事务，
流程中途可能挂起等人审批，几天后再恢复继续。
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .auth import CurrentUser, get_current_user
from ..workflows.checkpointer import get_checkpointer
from ..workflows.engine import get_workflow_engine, new_run_id, set_deps_provider
from ..workflows.loader import get_flow_registry

logger = logging.getLogger(__name__)
router = APIRouter()


class StartWorkflowRequest(BaseModel):
    flow_id: str = Field(..., description="流程 ID")
    user_input: str = Field(default="", description="初始用户输入")
    version: Optional[int] = Field(default=None, description="流程版本，默认最新版")
    context: Dict[str, Any] = Field(default_factory=dict, description="业务上下文（发起人等）")


class ResumeWorkflowRequest(BaseModel):
    # 审批恢复：{"approved": true, "comment": "同意"}
    # 槽位补充：{"slots": {"quantity": 100}} 或 {"user_input": "文本"}
    payload: Dict[str, Any] = Field(default_factory=dict, description="恢复载荷")


def _snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    """把图状态整理成对外快照"""
    interrupts = state.get("__interrupts__") or []
    return {
        "run_id": state.get("run_id", ""),
        "flow_id": state.get("flow_id", ""),
        "flow_version": state.get("flow_version", 0),
        "status": state.get("status", "unknown"),
        "current_step": state.get("current_step", ""),
        "slots": state.get("slots", {}) or {},
        "step_results": state.get("step_results", {}) or {},
        "pending_approval": state.get("pending_approval"),
        "pending_question": state.get("pending_question"),
        "error": state.get("error"),
        "metadata": state.get("metadata", {}) or {},
        "interrupts": interrupts,
    }


def _extract_interrupts(graph_state: Any) -> List[Dict[str, Any]]:
    """从 LangGraph 的 state snapshot 里提取 interrupt 载荷"""
    out: List[Dict[str, Any]] = []
    tasks = getattr(graph_state, "tasks", None) or []
    for task in tasks:
        interrupts = getattr(task, "interrupts", None) or []
        for it in interrupts:
            value = getattr(it, "value", None)
            if value is None and isinstance(it, dict):
                value = it.get("value")
            if isinstance(value, dict):
                out.append(value)
    return out


@router.get("/flows", summary="列出流程定义")
async def list_flows(user: CurrentUser = Depends(get_current_user)):
    """列出所有可用的流程定义（供前端流程选择器）"""
    registry = get_flow_registry()
    return {"flows": registry.list_flows()}


@router.get("/flows/{flow_id}", summary="查看流程定义详情")
async def get_flow(flow_id: str, user: CurrentUser = Depends(get_current_user)):
    registry = get_flow_registry()
    flow = registry.get(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="流程不存在")
    return {
        "id": flow.id,
        "name": flow.name or flow.id,
        "description": flow.description,
        "version": flow.version,
        "slots": [s.model_dump() for s in flow.slots],
        "steps": [s.model_dump() for s in flow.steps],
    }


@router.post("/start", summary="启动流程")
async def start_workflow(request: StartWorkflowRequest, user: CurrentUser = Depends(get_current_user)):
    """启动一个流程实例，返回 run_id 与当前状态

    若流程在槽位抽取或人工审批处挂起，会在返回中带上待处理信息。
    """
    engine = get_workflow_engine()
    registry = get_flow_registry()
    flow = registry.get(request.flow_id, request.version)
    if flow is None:
        raise HTTPException(status_code=404, detail="流程不存在")

    checkpointer = await get_checkpointer()
    graph = engine.get_graph(flow.id, flow.version, checkpointer=checkpointer)
    if graph is None:
        raise HTTPException(status_code=500, detail="流程图编译失败")

    run_id = new_run_id()
    initial_state = {
        "run_id": run_id,
        "flow_id": flow.id,
        "flow_version": flow.version,
        "slots": {},
        "step_results": {},
        "current_step": flow.first_step_id() or "",
        "goto": None,
        "pending_approval": None,
        "status": "running",
        "error": None,
        "metadata": {
            "username": user.username,
            "created_at": _now(),
            **(request.context or {}),
        },
        "user_input": request.user_input,
        "pending_question": None,
    }

    config = {"configurable": {"thread_id": run_id}}
    try:
        result = await graph.ainvoke(initial_state, config=config)
    except Exception as e:
        logger.exception(f"流程启动失败 {flow.id}")
        raise HTTPException(status_code=500, detail=f"流程启动失败: {e}")

    snap = await graph.aget_state(config)
    snapshot = _snapshot(result if isinstance(result, dict) else {})
    snapshot["interrupts"] = _extract_interrupts(snap)
    snapshot["next"] = snapshot["interrupts"][0] if snapshot["interrupts"] else None
    return snapshot


@router.post("/runs/{run_id}/resume", summary="恢复流程")
async def resume_workflow(run_id: str, request: ResumeWorkflowRequest, user: CurrentUser = Depends(get_current_user)):
    """恢复一个挂起的流程

    审批场景传 {"approved": true, "comment": "..."}；
    槽位补充传 {"slots": {...}} 或 {"user_input": "..."}。
    """
    engine = get_workflow_engine()
    checkpointer = await get_checkpointer()

    # 从 checkpoint 反查流程实例属于哪个 flow
    config = {"configurable": {"thread_id": run_id}}
    graph = None
    flow_id = None
    # 需要先知道 flow_id 才能取到对应的图
    registry = get_flow_registry()
    for flow in registry.list_flows():
        candidate = registry.get(flow["id"], flow["version"])
        if candidate is None:
            continue
        g = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
        if g is None:
            continue
        snap = await g.aget_state(config)
        values = getattr(snap, "values", None) or {}
        if values.get("run_id") == run_id:
            graph = g
            flow_id = candidate.id
            break

    if graph is None:
        raise HTTPException(status_code=404, detail="流程实例不存在或已过期")

    from langgraph.types import Command

    try:
        result = await graph.ainvoke(Command(resume=request.payload), config=config)
    except Exception as e:
        logger.exception(f"流程恢复失败 run_id={run_id}")
        raise HTTPException(status_code=500, detail=f"流程恢复失败: {e}")

    snap = await graph.aget_state(config)
    snapshot = _snapshot(result if isinstance(result, dict) else {})
    snapshot["interrupts"] = _extract_interrupts(snap)
    snapshot["next"] = snapshot["interrupts"][0] if snapshot["interrupts"] else None
    return snapshot


@router.get("/runs/{run_id}", summary="查看流程实例状态")
async def get_run(run_id: str, user: CurrentUser = Depends(get_current_user)):
    """查询流程实例当前状态"""
    engine = get_workflow_engine()
    checkpointer = await get_checkpointer()
    registry = get_flow_registry()

    config = {"configurable": {"thread_id": run_id}}
    for flow_meta in registry.list_flows():
        candidate = registry.get(flow_meta["id"], flow_meta["version"])
        if candidate is None:
            continue
        g = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
        if g is None:
            continue
        snap = await g.aget_state(config)
        values = getattr(snap, "values", None) or {}
        if values.get("run_id") == run_id:
            snapshot = _snapshot(values)
            snapshot["interrupts"] = _extract_interrupts(snap)
            snapshot["next"] = snapshot["interrupts"][0] if snapshot["interrupts"] else None
            return snapshot

    raise HTTPException(status_code=404, detail="流程实例不存在")


@router.get("/runs", summary="列出流程实例")
async def list_runs(
    limit: int = 50,
    status: Optional[str] = None,
    user: CurrentUser = Depends(get_current_user),
):
    """列出当前用户的流程实例"""
    engine = get_workflow_engine()
    checkpointer = await get_checkpointer()
    registry = get_flow_registry()

    config = {"configurable": {"thread_id": "*"}}
    results: List[Dict[str, Any]] = []
    for flow_meta in registry.list_flows():
        candidate = registry.get(flow_meta["id"], flow_meta["version"])
        if candidate is None:
            continue
        g = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
        if g is None:
            continue
        try:
            snaps = await g.aget_list(config, limit=limit)
        except Exception as e:
            logger.warning(f"列出流程实例失败 {candidate.id}: {e}")
            continue
        for s in snaps:
            values = getattr(s, "values", None) or {}
            meta = values.get("metadata", {}) or {}
            if meta.get("username") != user.username:
                continue
            item = _snapshot(values)
            item["interrupts"] = _extract_interrupts(s)
            results.append(item)

    if status:
        results = [r for r in results if r.get("status") == status]
    results.sort(key=lambda x: x.get("metadata", {}).get("created_at", ""), reverse=True)
    return {"runs": results[:limit]}


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat()
