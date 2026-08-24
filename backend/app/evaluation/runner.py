"""
评测运行器 - 核心编排逻辑
加载评测集 → 调 RAG API → RAGAS + 自定义指标打分 → LLM Judge → 生成报告 → 门禁判断
"""

from typing import Dict, List, Optional
from datetime import datetime
import asyncio
import httpx
import logging
import os
import json

from .dataset import EvalCase, load_dataset
from .ragas_evaluator import RAGASEvaluator, EvalSample
from .llm_judge import LLMJudge
from .metrics import HallucinationFree, CitationAccuracy, MultiTurnConsistency
from .report import CaseResult, EvalReport, generate_report, compare_reports
from .gate import EvalGate, GateConfig

logger = logging.getLogger(__name__)


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

    async def run(
        self,
        dataset_path: str = None,
        params: Dict = None,
        baseline_path: str = None,
        layer: str = None,
        include_stale: bool = False,
        use_db: bool = False,
    ) -> EvalReport:
        """执行完整评测流程"""
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

        logger.info(f"开始评测: {len(cases)} 条用例")
        case_results = []

        for i, case in enumerate(cases, 1):
            logger.info(f"[{i}/{len(cases)}] 评测: {case.id} - {case.question[:50]}...")
            try:
                result = await self._eval_single(case)
                case_results.append(result)
                status = "PASS" if result.passed else "FAIL"
                logger.info(f"  → {status} (score={result.overall_score:.1f})")
            except Exception as e:
                logger.error(f"  → ERROR: {e}")
                case_results.append(CaseResult(
                    case_id=case.id, question=case.question,
                    category=case.category, difficulty=case.difficulty,
                    answer=f"评测出错: {str(e)}", overall_score=0,
                    passed=False, fail_reasons=[str(e)],
                ))

        # 生成报告
        report = generate_report(case_results, config=params)

        # 门禁检查
        baseline = None
        if baseline_path and os.path.exists(baseline_path):
            baseline = self._load_baseline(baseline_path)

        report.gate_passed, report.gate_reasons = self.gate.check(report, baseline)

        return report

    async def _eval_single(self, case: EvalCase) -> CaseResult:
        """评测单条用例"""
        session_id = f"eval_{case.id}_{datetime.now().strftime('%H%M%S')}"

        # 1. 多轮场景：执行 setup_questions
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

        # 3. 规则检查
        rule_result = self._check_rules(case, answer, citations)

        # 4. RAGAS 评分
        ragas_scores = await self.ragas.evaluate_single(
            case.question, answer, contexts, case.ground_truth
        )

        # 5. 自定义指标
        halluc_result = await self.hallucination_metric.score(
            case.question, answer, contexts, case.expected_not_contains
        )
        citation_result = self.citation_metric.score(
            answer, citations, case.expected_source
        )
        consistency_result = await self.consistency_metric.score(
            case.question, answer, previous_qa
        )

        # 6. LLM Judge
        judge_result = await self.judge.judge(
            case.question, answer, case.ground_truth, contexts
        )

        # 7. 综合评分
        ragas_faith = ragas_scores.get("faithfulness", 0) * 10
        ragas_cp = ragas_scores.get("context_precision", 0) * 10
        ragas_cr = ragas_scores.get("context_recall", 0) * 10
        ragas_available = any(v > 0 for v in [ragas_faith, ragas_cp, ragas_cr])

        scores = {
            "rule": rule_result["score"],
            "hallucination": halluc_result["score"],
            "citation": citation_result["score"],
            "consistency": consistency_result["score"],
            "judge": judge_result.overall_score,
        }
        weights = {
            "rule": 1.0, "hallucination": 1.5, "citation": 1.0,
            "consistency": 0.8, "judge": 1.2,
        }

        # RAGAS 可用时加入权重，不可用时跳过（避免拉低分数）
        if ragas_available:
            scores["ragas_faithfulness"] = ragas_faith
            scores["ragas_context_precision"] = ragas_cp
            scores["ragas_context_recall"] = ragas_cr
            weights["ragas_faithfulness"] = 1.0
            weights["ragas_context_precision"] = 0.5
            weights["ragas_context_recall"] = 0.5

        total_weight = sum(weights.values())
        overall = sum(scores[k] * weights[k] for k in scores) / total_weight

        # 判断是否通过
        fail_reasons = []
        if overall < 6.0:
            fail_reasons.append(f"总分 {overall:.1f} < 6.0")
        if halluc_result["score"] < 6.0:
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
        """调用 RAG API"""
        url = f"{self.rag_api_url}/api/chat/query"
        payload = {"question": question, "session_id": session_id, "stream": False}

        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                resp = await client.post(url, json=payload)
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
