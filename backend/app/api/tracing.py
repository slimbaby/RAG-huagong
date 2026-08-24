"""
链路追踪API
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List, Dict, Any
import json
import glob
import os
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

TRACE_STORAGE_PATH = "./logs/traces"


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


@router.get("/traces", summary="追踪列表")
async def list_traces(session_id: Optional[str] = None, status: Optional[str] = None, limit: int = Query(default=50, ge=1, le=200)):
    traces = _load_all_traces(limit * 2)
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
    traces = _load_all_traces(500)
    for t in traces:
        if t.get("trace_id") == trace_id:
            return t
    raise HTTPException(status_code=404, detail="追踪记录不存在")


@router.get("/traces/{trace_id}/flamegraph", summary="火焰图数据")
async def get_flamegraph(trace_id: str):
    traces = _load_all_traces(500)
    for t in traces:
        if t.get("trace_id") == trace_id:
            spans = t.get("spans", [])
            return {"trace_id": trace_id, "spans": spans, "total_duration_ms": t.get("duration_ms", 0)}
    raise HTTPException(status_code=404, detail="追踪记录不存在")
