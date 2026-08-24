"""
RAG 评测 CLI 脚本

用法：
  docker exec langgraph_rag_backend /app/.venv/bin/python /app/eval_runner.py
  docker exec langgraph_rag_backend /app/.venv/bin/python /app/eval_runner.py --dataset ./evaluation/eval_dataset.json
  docker exec langgraph_rag_backend /app/.venv/bin/python /app/eval_runner.py --no-gate
"""

import argparse
import asyncio
import sys
import os
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("eval")


async def main():
    parser = argparse.ArgumentParser(description="RAG 评测工具")
    parser.add_argument("--dataset", default="./evaluation/eval_dataset.json",
                        help="评测集路径")
    parser.add_argument("--output", default="./evaluation/reports",
                        help="报告输出目录")
    parser.add_argument("--gate", action="store_true", default=True,
                        help="启用门禁检查")
    parser.add_argument("--no-gate", action="store_true",
                        help="禁用门禁检查")
    parser.add_argument("--baseline", default=None,
                        help="基线报告路径（用于回归检测）")
    parser.add_argument("--api-url", default="http://localhost:8000",
                        help="RAG API 地址")
    parser.add_argument("--layer", choices=["core", "document", "regression", "all"],
                        default="all", help="只运行指定层级的用例")
    parser.add_argument("--include-stale", action="store_true",
                        help="包含已失效的用例")
    args = parser.parse_args()

    # 导入项目模块
    sys.path.insert(0, "/app")
    from app.evaluation.runner import EvalRunner
    from app.evaluation.gate import GateConfig
    from app.core.config import settings
    from app.core.model_config import ModelManager
    from app.services.llm_service import LLMService

    # 初始化 LLM
    model_manager = ModelManager.from_env()
    llm_config = model_manager.get_best_available_model()
    llm_client = LLMService(
        api_key=llm_config.api_key,
        base_url=llm_config.api_base_url,
        model_name=llm_config.model_name,
        temperature=0.1,
    ) if llm_config else None

    if not llm_client:
        logger.error("LLM 未配置，无法运行评测")
        sys.exit(1)

    # 门禁配置
    gate_config = None
    if args.no_gate:
        gate_config = GateConfig(
            min_overall_score=0,
            min_hallucination_score=0,
            min_citation_score=0,
            min_ragas_faithfulness=0,
            max_single_fail=999,
        )

    # 创建 runner
    runner = EvalRunner(
        rag_api_url=args.api_url,
        llm_client=llm_client,
        gate_config=gate_config,
    )

    # 执行评测
    logger.info(f"评测集: {args.dataset}")
    logger.info(f"API 地址: {args.api_url}")
    report = await runner.run(
        dataset_path=args.dataset,
        params={"api_url": args.api_url},
        baseline_path=args.baseline,
        layer=args.layer,
        include_stale=args.include_stale,
    )

    # 输出报告
    report.print_summary()

    # 保存报告
    os.makedirs(args.output, exist_ok=True)
    report_path = os.path.join(args.output, f"{report.eval_id}.json")
    report.save(report_path)

    # 退出码
    if not report.gate_passed:
        logger.error("评测门禁未通过")
        sys.exit(1)
    else:
        logger.info("评测门禁通过")


if __name__ == "__main__":
    asyncio.run(main())
