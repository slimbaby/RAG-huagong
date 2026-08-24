"""
评测数据迁移脚本：JSON 文件 → PostgreSQL

用法：
  docker exec langgraph_rag_backend /app/.venv/bin/python /app/migrate_eval_to_db.py
"""

import asyncio
import json
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("migrate")


async def main():
    sys.path.insert(0, "/app")
    from app.core.database import get_pool

    pool = await get_pool()
    logger.info("PostgreSQL 连接池就绪")

    # ── 迁移评测用例 ──
    dataset_path = "./evaluation/eval_dataset.json"
    if os.path.exists(dataset_path):
        with open(dataset_path, "r", encoding="utf-8") as f:
            cases = json.load(f)
        async with pool.acquire() as conn:
            for c in cases:
                case_id = c.get("id", "")
                if not case_id:
                    continue
                await conn.execute(
                    """INSERT INTO eval_cases (id, data, layer, source_doc_id, stale)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (id) DO UPDATE SET
                       data=$2, layer=$3, source_doc_id=$4, stale=$5, updated_at=NOW()""",
                    case_id,
                    json.dumps(c, ensure_ascii=False),
                    c.get("layer", "core"),
                    c.get("source_doc_id", ""),
                    c.get("stale", False),
                )
        logger.info(f"评测用例迁移完成: {len(cases)} 条")
    else:
        logger.info("未找到 eval_dataset.json，跳过")

    # ── 迁移评测报告 ──
    reports_dir = "./evaluation/reports"
    if os.path.exists(reports_dir):
        report_files = [f for f in os.listdir(reports_dir) if f.endswith(".json")]
        count = 0
        async with pool.acquire() as conn:
            for fname in report_files:
                fpath = os.path.join(reports_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    eval_id = data.get("eval_id", "")
                    if not eval_id:
                        continue
                    await conn.execute(
                        """INSERT INTO eval_reports (eval_id, data, avg_score, gate_passed, total_cases, passed_cases)
                           VALUES ($1, $2, $3, $4, $5, $6)
                           ON CONFLICT (eval_id) DO UPDATE SET
                           data=$2, avg_score=$3, gate_passed=$4, total_cases=$5, passed_cases=$6""",
                        eval_id,
                        json.dumps(data, ensure_ascii=False),
                        data.get("avg_score", 0),
                        data.get("gate_passed", False),
                        data.get("total_cases", 0),
                        data.get("passed_cases", 0),
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"跳过 {fname}: {e}")
        logger.info(f"评测报告迁移完成: {count} 份")
    else:
        logger.info("未找到 reports 目录，跳过")

    # 验证
    async with pool.acquire() as conn:
        case_count = await conn.fetchval("SELECT COUNT(*) FROM eval_cases")
        report_count = await conn.fetchval("SELECT COUNT(*) FROM eval_reports")
    logger.info(f"验证: eval_cases={case_count} 条, eval_reports={report_count} 份")
    logger.info("迁移完成!")


if __name__ == "__main__":
    asyncio.run(main())
