"""
评测 API 端点
存储：PostgreSQL（用例 + 报告）、Redis（任务状态）
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Dict, List, Optional
import json
import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()


class EvalRunRequest(BaseModel):
    baseline_path: Optional[str] = None
    gate_enabled: bool = True
    layer: Optional[str] = None           # "core" / "document" / "regression" / None(全部)
    include_stale: bool = False           # 是否包含失效用例
    sample_ratio: Optional[float] = None  # 非 core 层抽样比例 (0.05~1.0)；None=用配置默认，1.0=全量


class EvalRunResponse(BaseModel):
    task_id: str
    message: str


class DatasetImportRequest(BaseModel):
    cases: List[Dict]
    name: str = "default"
    description: str = ""


# ── Redis 任务状态操作 ──────────────────────────────────────────

async def _set_eval_task(task_id: str, data: Dict):
    """写入评测任务状态到 Redis"""
    from ..core.config import get_redis
    redis = await get_redis()
    # 值全部转为字符串
    str_data = {k: str(v) for k, v in data.items()}
    await redis.hset(f"eval_task:{task_id}", mapping=str_data)
    await redis.expire(f"eval_task:{task_id}", 86400)


async def _get_eval_task(task_id: str) -> Optional[Dict]:
    """从 Redis 读取评测任务状态"""
    from ..core.config import get_redis
    redis = await get_redis()
    data = await redis.hgetall(f"eval_task:{task_id}")
    return data if data else None


async def _update_eval_task(task_id: str, data: Dict):
    """更新评测任务状态"""
    from ..core.config import get_redis
    redis = await get_redis()
    str_data = {k: str(v) for k, v in data.items()}
    await redis.hset(f"eval_task:{task_id}", mapping=str_data)


# ── API 端点 ──────────────────────────────────────────────────

@router.post("/run", response_model=EvalRunResponse, summary="执行评测")
async def run_evaluation(request: EvalRunRequest, background_tasks: BackgroundTasks):
    task_id = str(uuid.uuid4())[:8]
    await _set_eval_task(task_id, {
        "status": "running",
        "started_at": datetime.now().isoformat(),
    })
    background_tasks.add_task(_run_eval_task, task_id, request)
    return EvalRunResponse(task_id=task_id, message="评测已启动")


@router.get("/status/{task_id}", summary="查询评测状态")
async def get_eval_status(task_id: str):
    task = await _get_eval_task(task_id)
    if not task:
        raise HTTPException(404, "评测任务不存在")
    return task


@router.get("/report/{eval_id}", summary="查看评测报告")
async def get_eval_report(eval_id: str):
    from ..evaluation.report import EvalReport
    report = await EvalReport.load_from_db(eval_id)
    if not report:
        raise HTTPException(404, "报告不存在")
    return report.to_dict()


@router.get("/reports", summary="评测报告列表")
async def list_eval_reports():
    from ..evaluation.report import EvalReport
    reports = await EvalReport.list_from_db(limit=20)
    return {"reports": reports}


@router.get("/dataset", summary="查看当前评测集")
async def get_eval_dataset():
    from ..evaluation.dataset import load_cases_from_db
    cases = await load_cases_from_db()
    return {"cases": [c.to_dict() for c in cases]}


@router.put("/dataset", summary="更新评测集")
async def update_eval_dataset(cases: List[Dict]):
    from ..evaluation.dataset import EvalCase, save_cases_to_db
    eval_cases = []
    for c in cases:
        try:
            eval_cases.append(EvalCase.from_dict(c))
        except Exception as e:
            raise HTTPException(400, f"无效的评测用例: {e}")
    await save_cases_to_db(eval_cases)
    return {"message": f"评测集已更新: {len(eval_cases)} 条用例"}


@router.post("/dataset/import", summary="导入评测集")
async def import_eval_dataset(request: DatasetImportRequest):
    from ..evaluation.dataset import EvalCase, save_cases_to_db
    eval_cases = []
    for c in request.cases:
        try:
            eval_cases.append(EvalCase.from_dict(c))
        except Exception as e:
            raise HTTPException(400, f"无效的评测用例: {e}")
    await save_cases_to_db(eval_cases)
    return {"message": f"导入成功: {len(eval_cases)} 条用例"}


@router.post("/compare", summary="对比两次评测结果")
async def compare_eval_reports(eval_id_1: str, eval_id_2: str):
    from ..evaluation.report import EvalReport
    r1 = await EvalReport.load_from_db(eval_id_1)
    r2 = await EvalReport.load_from_db(eval_id_2)
    if not r1:
        raise HTTPException(404, f"报告不存在: {eval_id_1}")
    if not r2:
        raise HTTPException(404, f"报告不存在: {eval_id_2}")
    return {
        "report_1": {"eval_id": r1.eval_id, "avg_score": r1.avg_score},
        "report_2": {"eval_id": r2.eval_id, "avg_score": r2.avg_score},
        "score_change": round(r2.avg_score - r1.avg_score, 2),
    }


@router.get("/dataset/stats", summary="评测集统计")
async def get_eval_dataset_stats():
    from ..evaluation.dataset import load_cases_from_db
    cases = await load_cases_from_db()
    total = len(cases)
    by_layer = {}
    stale_count = 0
    for c in cases:
        by_layer[c.layer] = by_layer.get(c.layer, 0) + 1
        if c.stale:
            stale_count += 1
    return {
        "total": total,
        "by_layer": by_layer,
        "stale": stale_count,
        "active": total - stale_count,
    }


@router.get("/dataset/stale", summary="查看失效用例")
async def get_stale_cases():
    from ..evaluation.dataset import load_cases_from_db
    cases = await load_cases_from_db()
    stale_cases = [c.to_dict() for c in cases if c.stale]
    return {"count": len(stale_cases), "cases": stale_cases}


@router.post("/dataset/stale/{doc_id}", summary="标记指定文档的用例为失效")
async def mark_stale_by_doc_id(doc_id: str):
    from ..evaluation.dataset import mark_stale_by_doc_db
    marked = await mark_stale_by_doc_db(doc_id)
    return {"message": f"已标记 {marked} 条用例为失效", "doc_id": doc_id}


@router.post("/dataset/regenerate/{doc_id}", summary="重新生成指定文档的评测用例")
async def regenerate_eval_cases(doc_id: str):
    from ..evaluation.dataset import load_cases_from_db, save_cases_to_db, mark_stale_by_doc_db
    from ..evaluation.case_generator import EvalCaseGenerator
    from ..core.model_config import ModelManager
    from ..services.llm_service import LLMService
    from ..api.documents import _load_metadata

    # 获取文档信息
    meta = _load_metadata()
    doc = meta.get(doc_id)
    if not doc:
        raise HTTPException(404, f"文档不存在: {doc_id}")

    filename = doc.get("filename", "")

    # 初始化 LLM
    model_manager = ModelManager.from_env()
    llm_config = model_manager.get_best_available_model()
    if not llm_config:
        raise HTTPException(500, "LLM 未配置")
    llm = LLMService(
        api_key=llm_config.api_key,
        base_url=llm_config.api_base_url,
        model_name=llm_config.model_name,
        temperature=0.3,
    )

    # 获取文档 chunks
    from ..vectorstore.shared_store import get_shared_store
    store = get_shared_store()
    chunks = await store.query_by_file(filename)
    if not chunks:
        raise HTTPException(404, f"文档 {filename} 无索引数据")

    chunk_dicts = [{"content": c.get("content", ""), "metadata": c} for c in chunks[:50]]

    # 标记旧用例 stale
    await mark_stale_by_doc_db(doc_id)

    # 生成新用例
    generator = EvalCaseGenerator(llm_client=llm)
    new_cases = await generator.generate_from_chunks(doc_id, filename, chunk_dicts)

    if new_cases:
        from ..evaluation.dataset import add_cases_to_db
        await add_cases_to_db(new_cases)

    return {
        "message": f"重新生成完成: {len(new_cases)} 条新用例",
        "doc_id": doc_id,
        "new_cases": len(new_cases),
    }


async def _run_eval_task(task_id: str, request: EvalRunRequest):
    """后台执行评测"""
    try:
        from ..evaluation.runner import EvalRunner
        from ..evaluation.gate import GateConfig
        from ..core.model_config import ModelManager
        from ..services.llm_service import LLMService

        model_manager = ModelManager.from_env()
        llm_config = model_manager.get_best_available_model()
        llm_client = LLMService(
            api_key=llm_config.api_key,
            base_url=llm_config.api_base_url,
            model_name=llm_config.model_name,
            temperature=0.1,
        ) if llm_config else None

        gate_config = None
        if not request.gate_enabled:
            # 关闭门禁 = 全部阈值放行（GateConfig 没有 min_ragas_faithfulness 字段，传了会 TypeError）
            gate_config = GateConfig(min_overall_score=0, min_hallucination_score=0,
                                     min_citation_score=0,
                                     max_single_fail=999)

        runner = EvalRunner(rag_api_url="http://localhost:8000", llm_client=llm_client, gate_config=gate_config)
        report = await runner.run(
            dataset_path=None,  # 从数据库加载
            use_db=True,
            baseline_path=request.baseline_path,
            layer=request.layer,
            include_stale=request.include_stale,
            sample_ratio=request.sample_ratio,
        )

        # 报告保存到数据库
        await report.save_to_db()

        await _update_eval_task(task_id, {
            "status": "completed",
            "eval_id": report.eval_id,
            "avg_score": report.avg_score,
            "gate_passed": report.gate_passed,
        })
    except Exception as e:
        logger.error(f"评测任务失败: {e}", exc_info=True)
        await _update_eval_task(task_id, {"status": "failed", "error": str(e)})
