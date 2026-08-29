"""
RAGAS评估器 - 通过子进程调用 RAGAS，避免 torch/uvloop/nest_asyncio 冲突

主进程绝不 import ragas（历史上曾导致 segfault）：所有评分在
backend/ragas_worker.py 子进程中完成，崩溃/超时只影响当次评分，
优雅降级为 0 分。连续失败 3 次后本进程内自动停用，避免评测长时间空转。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import asyncio
import logging
import json
import math
import sys
import os

logger = logging.getLogger(__name__)

WORKER_CHUNK_SIZE = 10
MAX_CONSECUTIVE_FAILURES = 3
MAX_PARALLEL_WORKERS = 3  # 同时运行的 RAGAS 子进程数上限（每个约占 1GB 内存）


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
    """RAGAS评估器 - 子进程隔离运行，失败优雅降级为 0 分"""

    def __init__(self, config: Dict = None, llm_client=None):
        self.config = config or {}
        self.metrics = ["faithfulness", "context_precision", "context_recall"]
        self.llm = llm_client
        self._consecutive_failures = 0
        self._disabled = False
        # app/evaluation/ragas_evaluator.py -> backend/ragas_worker.py
        self._worker_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ragas_worker.py",
        )

    def _is_available(self) -> bool:
        from ..core.config import settings
        if not getattr(settings, "RAGAS_ENABLED", True):
            return False
        if self._disabled or self.llm is None:
            return False
        if not os.path.exists(self._worker_path):
            logger.warning(f"RAGAS worker 脚本不存在: {self._worker_path}")
            return False
        return True

    def _zero(self) -> Dict[str, float]:
        return {m: 0.0 for m in self.metrics}

    @staticmethod
    def _safe_float(v) -> float:
        """NaN/Inf/解析失败一律归 0（NaN 是 truthy，`or 0.0` 挡不住）"""
        try:
            f = float(v)
        except (TypeError, ValueError):
            return 0.0
        return f if math.isfinite(f) else 0.0

    async def _run_worker(self, samples: List[Dict]) -> Optional[List[Dict[str, float]]]:
        """运行子进程评分。返回每样本分数列表；失败返回 None（调用方降级为 0 分）。"""
        from ..core.config import settings

        client = getattr(self.llm, "client", None)
        api_key = getattr(client, "api_key", "") if client else ""
        base_url = str(getattr(client, "base_url", "")) if client else ""
        model = getattr(self.llm, "model_name", "")
        if not api_key or not base_url:
            logger.warning("RAGAS 跳过：LLM 客户端缺少 api_key/base_url")
            return None

        payload = json.dumps({
            "samples": samples,
            "api_key": api_key,
            "base_url": base_url,
            "model": model,
        }, ensure_ascii=False).encode("utf-8")

        timeout = getattr(settings, "RAGAS_TIMEOUT_SECONDS", 600)
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, self._worker_path,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            self._register_failure(f"启动 RAGAS 子进程失败: {e}")
            return None

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.wait()
            except ProcessLookupError:
                pass
            self._register_failure(f"RAGAS 子进程超时 (> {timeout}s)，已终止")
            return None
        except Exception as e:
            proc.kill()
            self._register_failure(f"RAGAS 子进程通信失败: {e}")
            return None

        if proc.returncode != 0:
            # segfault 等崩溃表现为非零/negative returncode
            err_tail = (stderr or b"")[-500:].decode("utf-8", errors="replace")
            self._register_failure(
                f"RAGAS 子进程异常退出 (returncode={proc.returncode}): {err_tail}"
            )
            return None

        try:
            data = json.loads(stdout.decode("utf-8"))
            scores = data["scores"]
            if len(scores) != len(samples):
                raise ValueError(f"返回分数数量不匹配: {len(scores)} != {len(samples)}")
            self._consecutive_failures = 0
            return [
                {m: self._safe_float(s.get(m, 0.0)) for m in self.metrics}
                for s in scores
            ]
        except Exception as e:
            self._register_failure(f"RAGAS 子进程输出解析失败: {e}")
            return None

    def _register_failure(self, reason: str):
        self._consecutive_failures += 1
        logger.warning(f"RAGAS 评分失败 ({self._consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}): {reason}")
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._disabled = True
            logger.error(
                f"RAGAS 连续失败 {MAX_CONSECUTIVE_FAILURES} 次，本进程内已自动停用"
                f"（相关指标记 0 分，重置需重启服务或检查 RAGAS 环境）"
            )

    async def _evaluate_many(self, samples: List[EvalSample]) -> List[Dict[str, float]]:
        if not samples:
            return []
        if not self._is_available():
            return [self._zero() for _ in samples]

        chunks = [
            samples[i:i + WORKER_CHUNK_SIZE]
            for i in range(0, len(samples), WORKER_CHUNK_SIZE)
        ]
        # chunk 间并发（每个 chunk 一个子进程），限制并发数防止内存爆炸
        sem = asyncio.Semaphore(MAX_PARALLEL_WORKERS)

        async def _eval_chunk(chunk):
            async with sem:
                payload = [
                    {
                        "question": s.question,
                        "answer": s.answer,
                        "contexts": s.contexts,
                        "ground_truth": s.ground_truth,
                    }
                    for s in chunk
                ]
                scores = await self._run_worker(payload)
                if scores is None:
                    return [self._zero() for _ in chunk]
                return scores

        chunk_results = await asyncio.gather(*[_eval_chunk(c) for c in chunks])
        results: List[Dict[str, float]] = []
        for cr in chunk_results:
            results.extend(cr)
        return results

    async def evaluate_single(self, question: str, answer: str,
                               contexts: List[str], ground_truth: str = "") -> Dict[str, float]:
        """单条评分（子进程隔离 + 超时保护，失败降级为 0 分）"""
        scores = await self._evaluate_many([
            EvalSample(question=question, answer=answer,
                       contexts=contexts, ground_truth=ground_truth)
        ])
        return scores[0] if scores else self._zero()

    async def evaluate_batch(self, samples: List[EvalSample]) -> EvalResult:
        """批量评分：按 chunk 合并子进程调用，降低进程创建开销"""
        all_scores = await self._evaluate_many(samples)

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
