"""
检查点API

基于 LangGraph 官方 AsyncSqliteSaver 的流程实例查询。

（原实现是一组空壳：全部 raise 404 / return {}，前端从未调用；
流程的启动/恢复/查询职责由 app/api/workflows.py 接管，
这里只做 checkpoint 层面的只读查询与历史回溯。）
"""

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException, Query

from ..workflows.checkpointer import get_checkpointer
from ..workflows.engine import get_workflow_engine
from ..workflows.loader import get_flow_registry

logger = logging.getLogger(__name__)
router = APIRouter()


async def _find_graph(thread_id: str):
    """找到承载该 thread 的流程图"""
    engine = get_workflow_engine()
    checkpointer = await get_checkpointer()
    registry = get_flow_registry()

    config = {"configurable": {"thread_id": thread_id}}
    for flow_meta in registry.list_flows():
        candidate = registry.get(flow_meta["id"], flow_meta["version"])
        if candidate is None:
            continue
        graph = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
        if graph is None:
            continue
        snapshot = await graph.aget_state(config)
        values = getattr(snapshot, "values", None) or {}
        if values:
            return graph, snapshot
    return None, None


@router.get("/threads", summary="列出 checkpoint 线程")
async def list_threads(limit: int = Query(default=50, ge=1, le=200)):
    """列出所有存在 checkpoint 的线程（流程实例）"""
    engine = get_workflow_engine()
    checkpointer = await get_checkpointer()
    registry = get_flow_registry()

    config = {"configurable": {"thread_id": "*"}}
    results: List[Dict[str, Any]] = []
    for flow_meta in registry.list_flows():
        candidate = registry.get(flow_meta["id"], flow_meta["version"])
        if candidate is None:
            continue
        graph = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
        if graph is None:
            continue
        try:
            snapshots = await graph.aget_list(config, limit=limit)
        except Exception as e:
            logger.warning(f"列出线程失败 {candidate.id}: {e}")
            continue
        for s in snapshots:
            values = getattr(s, "values", None) or {}
            results.append({
                "thread_id": getattr(s, "config", {}).get("configurable", {}).get("thread_id", ""),
                "run_id": values.get("run_id", ""),
                "flow_id": values.get("flow_id", ""),
                "status": values.get("status", ""),
                "current_step": values.get("current_step", ""),
                "created_at": (values.get("metadata", {}) or {}).get("created_at", ""),
            })
    return {"threads": results[:limit]}


@router.get("/threads/{thread_id}", summary="查看 checkpoint 详情")
async def get_thread(thread_id: str):
    """查看某个线程的 checkpoint 快照"""
    graph, snapshot = await _find_graph(thread_id)
    if graph is None:
        raise HTTPException(status_code=404, detail="checkpoint 不存在")

    values = getattr(snapshot, "values", None) or {}
    tasks = getattr(snapshot, "tasks", None) or []
    return {
        "thread_id": thread_id,
        "values": values,
        "next": getattr(snapshot, "next", None),
        "pending_tasks": [
            {
                "name": getattr(t, "name", ""),
                "interrupts": [
                    getattr(i, "value", None) for i in (getattr(t, "interrupts", None) or [])
                ],
            }
            for t in tasks
        ],
    }


@router.get("/threads/{thread_id}/history", summary="checkpoint 历史")
async def get_thread_history(thread_id: str, limit: int = Query(default=20, ge=1, le=100)):
    """查看某个线程的 checkpoint 状态历史"""
    engine = get_workflow_engine()
    checkpointer = await get_checkpointer()
    registry = get_flow_registry()

    config = {"configurable": {"thread_id": thread_id}}
    for flow_meta in registry.list_flows():
        candidate = registry.get(flow_meta["id"], flow_meta["version"])
        if candidate is None:
            continue
        graph = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
        if graph is None:
            continue
        try:
            history = await graph.aget_state_history(config, limit=limit)
        except Exception:
            continue
        if not history:
            continue
        return {
            "thread_id": thread_id,
            "history": [
                {
                    "checkpoint_id": getattr(h, "config", {}).get("configurable", {}).get("checkpoint_id", ""),
                    "step": getattr(h, "metadata", {}).get("step", 0),
                    "source": getattr(h, "metadata", {}).get("source", ""),
                    "next": getattr(h, "next", None),
                    "values": getattr(h, "values", None) or {},
                }
                for h in history
            ],
        }

    raise HTTPException(status_code=404, detail="checkpoint 历史不存在")
