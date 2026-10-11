"""
韧性保护API - 熔断器状态查看与运维操作

2026-09-25 起接入真实执行器（app/resilience/executor.py 全局单例）：
llm / milvus / bm25 三条依赖路径的熔断状态在此可见、可重置。
此前本文件全部是返回空对象的桩。
"""

from fastapi import APIRouter, HTTPException
from typing import Optional, Dict
from pydantic import BaseModel

from ..core.resilience_config import ResilienceConfig, resilience_config
from ..resilience.executor import get_executor, get_manager

router = APIRouter()


class ConfigUpdateRequest(BaseModel):
    updates: Dict


@router.get("/status", summary="韧性状态")
async def get_resilience_status():
    return get_manager().get_all_status()


@router.get("/circuit-breakers", summary="熔断器状态")
async def get_circuit_breakers():
    return get_manager().circuit_breaker_manager.get_all_stats()


def _require_breaker(name: str):
    stats = get_manager().circuit_breaker_manager.get_all_stats()
    if name not in stats:
        raise HTTPException(status_code=404, detail=f"熔断器 '{name}' 不存在（现有: {sorted(stats)}）")
    return get_manager().get_circuit_breaker(name)


@router.post("/circuit-breakers/{name}/reset", summary="重置熔断器")
async def reset_circuit_breaker(name: str):
    breaker = _require_breaker(name)
    breaker.reset()
    return {"status": "reset", "name": name, "state": breaker.state.value}


@router.post("/circuit-breakers/{name}/open", summary="强制打开熔断器")
async def force_open_circuit_breaker(name: str):
    breaker = _require_breaker(name)
    breaker.force_open()
    return {"status": "opened", "name": name, "state": breaker.state.value}


@router.post("/circuit-breakers/reset-all", summary="重置所有熔断器")
async def reset_all_circuit_breakers():
    get_manager().reset_all()
    return {"status": "all_reset"}


@router.get("/config", summary="获取配置")
async def get_resilience_config():
    return get_executor().config.model_dump()


@router.put("/config", summary="更新配置（进程内生效，不持久化）")
async def update_resilience_config(request: ConfigUpdateRequest):
    """动态更新韧性配置。注意：已创建的熔断器实例保留原阈值，
    新参数对之后创建的熔断器生效；重启后恢复为 YAML/默认配置。"""
    try:
        new_config = get_executor().config.update(**request.updates)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"配置更新失败: {e}")
    get_executor().apply_config(new_config)
    return {"status": "updated", "config": new_config.model_dump()}
