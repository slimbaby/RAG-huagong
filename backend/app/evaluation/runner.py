"""
评测运行器 - 核心编排逻辑
加载评测集 → 调 RAG API → RAGAS + 自定义指标打分 → LLM Judge → 生成报告 → 门禁判断
"""

from typing import Dict, List, Optional
from datetime import datetime
from dataclasses import dataclass, field
import asyncio
import httpx
import logging
import os
import json
import random

from .dataset import EvalCase, load_dataset
from .ragas_evaluator import RAGASEvaluator, EvalSample
from .llm_judge import LLMJudge
from .metrics import HallucinationFree, CitationAccuracy, MultiTurnConsistency
from .report import CaseResult, EvalReport, generate_report, compare_reports
from .gate import EvalGate, GateConfig

logger = logging.getLogger(__name__)


@dataclass
class _CaseIntermediate:
    """阶段1产物：问答+独立指标已完成，待 RAGAS 批量评分后合并出分"""
    case: EvalCase
    answer: str
    citations: List[Dict]
    contexts: List[str]
    rule_result: Dict
    halluc_result: Dict
    citation_result: Dict
    consistency_result: Dict
    judge_result: Optional[object]
    previous_qa: List[Dict] = field(default_factory=list)
    error: Optional[str] = None


class EvalRunner:
    """评测运行器"""

    def __init__(
        self,
        rag_api_url: str = "http://localhost:8000",
        llm_client=None,
        gate_config: GateConfig = None,
    ):
        self.rag_api_url = rag_api_url.rstrip("/")
        self.llm = llm_client
        self.ragas = RAGASEvaluator(llm_client=llm_client)
        self.judge = LLMJudge(llm_client=llm_client)
        self.hallucination_metric = HallucinationFree(llm_client=llm_client)
        self.citation_metric = CitationAccuracy()
        self.consistency_metric = MultiTurnConsistency(llm_client=llm_client)
        self.gate = EvalGate(gate_config)

        from ..core.config import settings
        self.concurrency = max(1, getattr(settings, "EVAL_CONCURRENCY", 3))

    async def run(
        self,
        dataset_path: str = None,
        params: Dict = None,
        baseline_path: str = None,
        layer: str = None,
        include_stale: bool = False,
        use_db: bool = False,
        sample_ratio: Optional[float] = None,
    ) -> EvalReport:
        """执行完整评测流程（三阶段：并发问答+独立指标 → 批量RAGAS → 合并出分）"""
        from ..core.config import settings

        if use_db:
            from .dataset import load_cases_from_db
            cases = await load_cases_from_db()
        else:
            cases = load_dataset(dataset_path or "./evaluation/eval_dataset.json")
        if not cases:
            logger.error("评测集为空")
            return generate_report([])

        # 过滤 stale 用例
        if not include_stale:
            active = [c for c in cases if not c.stale]
            skipped = len(cases) - len(active)
            if skipped:
                logger.info(f"跳过 {skipped} 条失效用例")
            cases = active

        # 按层级过滤
        if layer and layer != "all":
            cases = [c for c in cases if c.layer == layer]
            logger.info(f"按层级过滤: {layer}，剩余 {len(cases)} 条用例")

        if not cases:
            logger.warning("过滤后无可用用例")
            return generate_report([])

        # 分层抽样：core 层始终全量（回归基线），其他层按比例抽样
        if sample_ratio is None:
            sample_ratio = getattr(settings, "EVAL_SAMPLE_RATIO", 0.2)
        sample_ratio = min(max(float(sample_ratio), 0.05), 1.0)
        sampled_out = 0
        if sample_ratio < 1.0:
            core_cases = [c for c in cases if c.layer == "core"]
            other_cases = [c for c in cases if c.layer != "core"]
            if other_cases:
                rng = random.Random(getattr(settings, "EVAL_SAMPLE_SEED", 20260828))
                n_keep = max(1, round(len(other_cases) * sample_ratio))
                cases = core_cases + rng.sample(other_cases, n_keep)
                sampled_out = len(other_cases) - n_keep
                logger.info(
                    f"非core层抽样: {len(other_cases)} -> {n_keep} 条 "
                    f"(ratio={sample_ratio}, seed={settings.EVAL_SAMPLE_SEED}, 抽掉 {sampled_out} 条)"
                )

        run_params = dict(params or {})
        run_params["sample_ratio"] = sample_ratio
        run_params["sampled_out"] = sampled_out
        run_params["concurrency"] = self.concurrency

        logger.info(f"开始评测: {len(cases)} 条用例 (并发={self.concurrency})")

        # ── 阶段1: 并发执行问答 + 独立指标（规则/幻觉/一致性/Judge） ──
        sem = asyncio.Semaphore(self.concurrency)

        async def _run_one(idx_case):
            idx, case = idx_case
            async with sem:
                logger.info(f"[{idx}/{len(cases)}] 评测: {case.id} - {case.question[:50]}...")
                try:
                    return await self._run_case(case)
                except Exception as e:
                    logger.error(f"  → ERROR: {case.id}: {e}")
                    return _CaseIntermediate(
                        case=case, answer=f"评测出错: {str(e)}", citations=[], contexts=[],
                        rule_result={}, halluc_result={"failed": True, "score": None},
                        citation_result={}, consistency_result={"failed": True, "score": None},
                        judge_result=None, error=str(e),
                    )

        intermediates = list(await asyncio.gather(
            *[_run_one((i, c)) for i, c in enumerate(cases, 1)]
        ))

        # ── 阶段2: 批量 RAGAS（chunk 合并子进程调用，chunk 间并发） ──
        pending = [x for x in intermediates if not x.error]
        ragas_per_case: List[Dict[str, float]] = []
        if pending:
            samples = [
                EvalSample(
                    question=x.case.question, answer=x.answer,
                    contexts=x.contexts, ground_truth=x.case.ground_truth,
                )
                for x in pending
            ]
            ragas_batch = await self.ragas.evaluate_batch(samples)
            ragas_per_case = ragas_batch.per_sample_scores
        ragas_map = {id(x): s for x, s in zip(pending, ragas_per_case)}

        # ── 阶段3: 合并 RAGAS 分数，计算总分与通过判定 ──
        case_results = []
        for x in intermediates:
            if x.error:
                case_results.append(CaseResult(
                    case_id=x.case.id, question=x.case.question,
                    category=x.case.category, difficulty=x.case.difficulty,
                    answer=x.answer, overall_score=0,
                    passed=False, fail_reasons=[x.error],
                ))
                continue
            try:
                result = self._finalize_case(x, ragas_map.get(id(x), {}))
                case_results.append(result)
                status = "PASS" if result.passed else "FAIL"
                logger.info(f"  → {x.case.id}: {status} (score={result.overall_score:.1f})")
            except Exception as e:
                logger.error(f"  → 合并评分失败 {x.case.id}: {e}")
                case_results.append(CaseResult(
                    case_id=x.case.id, question=x.case.question,
                    category=x.case.category, difficulty=x.case.difficulty,
                    answer=x.answer, overall_score=0,
                    passed=False, fail_reasons=[f"合并评分失败: {e}"],
                ))

        # 生成报告
        report = generate_report(case_results, config=run_params)

        # 门禁检查
        baseline = None
        if baseline_path and os.path.exists(baseline_path):
            baseline = self._load_baseline(baseline_path)

        report.gate_passed, report.gate_reasons = self.gate.check(report, baseline)

        return report

    async def _run_case(self, case: EvalCase) -> _CaseIntermediate:
        """阶段1：执行问答（含多轮 setup）+ 规则检查 + 并发跑独立指标"""
        session_id = f"eval_{case.id}_{datetime.now().strftime('%H%M%S')}"

        # 1. 多轮场景：执行 setup_questions（必须串行，后续轮次依赖前面的对话上下文）
        previous_qa = []
        for sq in (case.setup_questions or []):
            resp = await self._call_rag_api(sq, session_id)
            previous_qa.append({"question": sq, "answer": resp.get("answer", "")})

        # 2. 执行目标问题
        response = await self._call_rag_api(case.question, session_id)
        answer = response.get("answer", "")
        citations = response.get("citations", [])
        # 使用完整内容而非 preview，避免幻觉检测误判
        contexts = []
        for c in citations:
            content = c.get("content", "") or c.get("content_preview", "")
            if content:
                contexts.append(content)

        # 3. 规则检查（纯规则，瞬时完成）
        rule_result = self._check_rules(case, answer, citations)
        citation_result = self.citation_metric.score(answer, citations, case.expected_source)

        # 4. 三个独立 LLM 指标并发执行（RAGAS 留到阶段2批量跑）
        halluc_result, consistency_result, judge_result = await asyncio.gather(
            self.hallucination_metric.score(
                case.question, answer, contexts, case.expected_not_contains
            ),
            self.consistency_metric.score(case.question, answer, previous_qa),
            self.judge.judge(case.question, answer, case.ground_truth, contexts),
        )

        return _CaseIntermediate(
            case=case, answer=answer, citations=citations, contexts=contexts,
            rule_result=rule_result, halluc_result=halluc_result,
            citation_result=citation_result, consistency_result=consistency_result,
            judge_result=judge_result, previous_qa=previous_qa,
        )

    def _finalize_case(self, x: _CaseIntermediate, ragas_scores: Dict[str, float]) -> CaseResult:
        """阶段3：合并 RAGAS 批量分数，计算加权总分与通过判定"""
        case, answer, citations = x.case, x.answer, x.citations
        rule_result = x.rule_result
        halluc_result = x.halluc_result
        citation_result = x.citation_result
        consistency_result = x.consistency_result
        judge_result = x.judge_result

        # 综合评分（失败的指标排除出加权总分，不再用中性分冒充真实成绩）
        ragas_faith = ragas_scores.get("faithfulness", 0) * 10
        ragas_cp = ragas_scores.get("context_precision", 0) * 10
        ragas_cr = ragas_scores.get("context_recall", 0) * 10
        ragas_available = any(v > 0 for v in [ragas_faith, ragas_cp, ragas_cr])

        scores = {"rule": rule_result["score"]}
        weights = {"rule": 1.0}
        metric_failures = []

        def _add_metric(name: str, result: Dict, weight: float):
            if result.get("failed") or result.get("score") is None:
                metric_failures.append(f"{name}: {result.get('fail_reason', '未知原因')}")
                return
            scores[name] = result["score"]
            weights[name] = weight

        _add_metric("hallucination", halluc_result, 1.5)
        _add_metric("consistency", consistency_result, 0.8)
        # 引用检查是纯规则计算，不会失败
        scores["citation"] = citation_result["score"]
        weights["citation"] = 1.0
        if judge_result is not None:
            scores["judge"] = judge_result.overall_score
            weights["judge"] = 1.2

        if ragas_available:
            scores["ragas_faithfulness"] = ragas_faith
            scores["ragas_context_precision"] = ragas_cp
            scores["ragas_context_recall"] = ragas_cr
            weights["ragas_faithfulness"] = 1.0
            weights["ragas_context_precision"] = 0.5
            weights["ragas_context_recall"] = 0.5

        if not scores:
            overall = 0.0
            fail_reasons_all = ["所有评分指标均失败，无法评估"]
        else:
            total_weight = sum(weights.values())
            overall = sum(scores[k] * weights[k] for k in scores) / total_weight

        # 判断是否通过
        fail_reasons = []
        if overall < 6.0:
            fail_reasons.append(f"总分 {overall:.1f} < 6.0")
        if not halluc_result.get("failed") and halluc_result["score"] < 6.0:
            fail_reasons.append(f"幻觉分 {halluc_result['score']:.1f} < 6.0")
        if rule_result["score"] < 5.0:
            fail_reasons.append(f"规则检查未通过: {rule_result.get('reasons', [])}")

        return CaseResult(
            case_id=case.id,
            question=case.question,
            category=case.category,
            difficulty=case.difficulty,
            answer=answer[:500],
            rule_score=rule_result["score"],
            ragas_faithfulness=round(ragas_scores.get("faithfulness", 0) * 10, 2),
            ragas_context_precision=round(ragas_scores.get("context_precision", 0) * 10, 2),
            ragas_context_recall=round(ragas_scores.get("context_recall", 0) * 10, 2),
            hallucination_score=halluc_result["score"],
            citation_score=citation_result["score"],
            consistency_score=consistency_result["score"],
            judge_score=judge_result.overall_score,
            overall_score=round(overall, 2),
            passed=len(fail_reasons) == 0,
            fail_reasons=fail_reasons,
            details={
                "rule": rule_result,
                "hallucination": halluc_result,
                "citation": citation_result,
                "consistency": consistency_result,
                "ragas": ragas_scores,
                "metric_failures": metric_failures,
            },
        )

    def _check_rules(self, case: EvalCase, answer: str, citations: List[Dict]) -> Dict:
        """规则检查：expected_contains / expected_not_contains / expected_source"""
        score = 10.0
        reasons = []

        # expected_contains（任一匹配即通过）
        if case.expected_contains:
            if not any(kw in answer for kw in case.expected_contains):
                score -= 5.0
                reasons.append(f"答案未包含 {case.expected_contains} 中任一关键词")

        # expected_not_contains（全部不能出现）
        if case.expected_not_contains:
            found = [kw for kw in case.expected_not_contains if kw in answer]
            if found:
                score -= 5.0
                reasons.append(f"答案包含禁止内容: {found}")

        # expected_source
        if case.expected_source:
            source_found = False
            for c in citations:
                if case.expected_source in c.get("source_file", ""):
                    source_found = True
                    break
            if not source_found:
                import re
                cited = re.findall(r'\[来源\d+[:\s]([^\]]+)\]', answer)
                for src in cited:
                    if case.expected_source in src:
                        source_found = True
                        break
            if not source_found:
                score -= 3.0
                reasons.append(f"未引用期望来源 {case.expected_source}")

        return {"score": max(0, score), "reasons": reasons}

    async def _call_rag_api(self, question: str, session_id: str) -> Dict:
        """调用 RAG API（带内部服务 token，chat 接口已启用 JWT 认证）"""
        from ..core.security import create_access_token

        url = f"{self.rag_api_url}/api/chat/query"
        payload = {"question": question, "session_id": session_id, "stream": False}
        headers = {"Authorization": f"Bearer {create_access_token('eval_runner')}"}

        async with httpx.AsyncClient(timeout=240.0) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.error(f"RAG API 调用失败: {e}")
                return {"answer": f"API调用失败: {e}", "citations": []}

    def _load_baseline(self, path: str) -> Optional[EvalReport]:
        """加载基线报告"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            case_results = [
                CaseResult(
                    case_id=r["case_id"],
                    question=r.get("question", ""),
                    category=r.get("category", ""),
                    difficulty=r.get("difficulty", ""),
                    answer=r.get("answer", ""),
                    overall_score=r.get("overall_score", 0),
                    passed=r.get("passed", True),
                    hallucination_score=r.get("scores", {}).get("hallucination", 0),
                    citation_score=r.get("scores", {}).get("citation", 0),
                )
                for r in data.get("case_results", [])
            ]
            return EvalReport(
                eval_id=data.get("eval_id", "baseline"),
                timestamp=data.get("timestamp", ""),
                total_cases=data.get("total_cases", 0),
                passed_cases=data.get("passed_cases", 0),
                failed_cases=data.get("failed_cases", 0),
                avg_score=data.get("avg_score", 0),
                dimension_scores=data.get("dimension_scores", {}),
                case_results=case_results,
            )
        except Exception as e:
            logger.warning(f"加载基线报告失败: {e}")
            return None
