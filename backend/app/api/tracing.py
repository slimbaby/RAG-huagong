"""
链路追踪API

存储格式（方案C：摘要与详情分离）：
- logs/traces/traces_YYYY-MM-DD.jsonl  轻量摘要行，列表接口专用，单行仅几百字节
- logs/traces/details/{trace_id}.json  完整数据（含 spans），详情接口 O(1) 直读
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
import asyncio
import json
import glob
import os
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

TRACE_STORAGE_PATH = "./logs/traces"
DETAILS_DIR = os.path.join(TRACE_STORAGE_PATH, "details")


def _load_all_traces(limit: int = 100) -> List[Dict[str, Any]]:
    traces = []
    if not os.path.exists(TRACE_STORAGE_PATH):
        return traces
    files = sorted(glob.glob(f"{TRACE_STORAGE_PATH}/traces_*.jsonl"), reverse=True)
    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            traces.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            continue
        if len(traces) >= limit:
            break
    traces.sort(key=lambda t: t.get("start_time", 0), reverse=True)
    return traces[:limit]


def _load_trace_detail(trace_id: str) -> Optional[Dict[str, Any]]:
    """优先 O(1) 直读详情文件；旧数据（摘要混存大文件）走全量扫描兜底"""
    detail_path = os.path.join(DETAILS_DIR, f"{trace_id}.json")
    if os.path.exists(detail_path):
        try:
            with open(detail_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"加载trace详情失败 {trace_id}: {e}")
            return None
    for file_path in sorted(glob.glob(f"{TRACE_STORAGE_PATH}/traces_*.jsonl"), reverse=True):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("trace_id") == trace_id:
                        return data
        except Exception:
            continue
    return None


@router.get("/traces", summary="追踪列表")
async def list_traces(session_id: Optional[str] = None, status: Optional[str] = None, limit: int = Query(default=50, ge=1, le=200)):
    # 磁盘 I/O 放入线程池，不阻塞事件循环
    traces = await asyncio.to_thread(_load_all_traces, limit * 2)
    if session_id:
        traces = [t for t in traces if t.get("session_id") == session_id]
    if status:
        traces = [t for t in traces if t.get("status") == status]
    result = []
    for t in traces[:limit]:
        result.append({
            "trace_id": t.get("trace_id", ""), "task_id": t.get("task_id"),
            "user_query": t.get("user_query", ""), "optimized_query": t.get("optimized_query"),
            "status": t.get("status", "unknown"), "duration_ms": t.get("duration_ms"),
            "sources_count": t.get("sources_count", 0),
            "tokens_used": t.get("tokens_used", {"prompt": 0, "completion": 0}),
            "start_time": t.get("start_time"),
            "answer": (t.get("answer") or "")[:200],
        })
    return result


@router.get("/traces/{trace_id}", summary="追踪详情")
async def get_trace(trace_id: str):
    trace = await asyncio.to_thread(_load_trace_detail, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="追踪记录不存在")
    return trace


@router.get("/traces/{trace_id}/flamegraph", summary="火焰图数据")
async def get_flamegraph(trace_id: str):
    trace = await asyncio.to_thread(_load_trace_detail, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="追踪记录不存在")
    return {
        "trace_id": trace_id,
        "spans": trace.get("spans", []),
        "total_duration_ms": trace.get("duration_ms", 0),
    }
