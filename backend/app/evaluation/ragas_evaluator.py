"""
RAGAS评估器 - 通过子进程调用 RAGAS，避免 uvloop/nest_asyncio 冲突
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import asyncio
import logging
import json
import sys
import os

logger = logging.getLogger(__name__)


@dataclass
class EvalSample:
    question: str
    answer: str
    contexts: List[str]
    ground_truth: str = ""
    metadata: Dict = field(default_factory=dict)


@dataclass
class EvalResult:
    eval_id: str
    timestamp: str
    sample_count: int
    scores: Dict[str, float]
    per_sample_scores: List[Dict[str, float]]
    config: Dict


class RAGASEvaluator:
    """RAGAS评估器 - 通过子进程运行 RAGAS，完全隔离事件循环"""

    def __init__(self, config: Dict = None, llm_client=None):
        self.config = config or {}
        self.metrics = ["faithfulness", "context_precision", "context_recall"]
        self.llm = llm_client
        self._ragas_available = None  # lazy check
        self._worker_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ragas_worker.py",
        )

    def _check_ragas(self) -> bool:
        """检查 RAGAS 是否可用"""
        if self._ragas_available is not None:
            return self._ragas_available
        try:
            import ragas
            self._ragas_available = True
            logger.info(f"RAGAS 可用: v{ragas.__version__}")
        except ImportError as e:
            logger.warning(f"RAGAS 不可用: {e}")
            self._ragas_available = False
        return self._ragas_available

    async def evaluate_single(self, question: str, answer: str,
                               contexts: List[str], ground_truth: str = "") -> Dict[str, float]:
        """单条评分 - RAGAS 暂时禁用（子进程 segfault 问题）"""
        # RAGAS 在当前环境下会 segfault，暂时返回 0 分
        # 自定义指标（规则检查、幻觉检测、引用准确性、LLM Judge）仍正常工作
        return {m: 0.0 for m in self.metrics}

    async def evaluate_batch(self, samples: List[EvalSample]) -> EvalResult:
        """批量评分"""
        all_scores = []
        for sample in samples:
            scores = await self.evaluate_single(
                sample.question, sample.answer, sample.contexts, sample.ground_truth
            )
            all_scores.append(scores)

        avg_scores = {}
        for metric in self.metrics:
            vals = [s.get(metric, 0) for s in all_scores]
            avg_scores[metric] = round(sum(vals) / len(vals), 4) if vals else 0.0

        return EvalResult(
            eval_id=f"ragas_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            timestamp=datetime.now().isoformat(),
            sample_count=len(samples),
            scores=avg_scores,
            per_sample_scores=all_scores,
            config=self.config,
        )
