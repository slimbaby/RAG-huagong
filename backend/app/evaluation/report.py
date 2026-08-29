"""
评测报告生成
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import json
import math
import os
import logging

logger = logging.getLogger(__name__)


def _sanitize_json(obj):
    """递归替换非有限浮点数 (NaN/Inf) 为 0.0。

    Python json.dumps 默认输出 NaN 字面量，PostgreSQL jsonb 会拒绝，
    导致整个评测报告保存失败（异常路径下指标可能产生 NaN）。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else 0.0
    if isinstance(obj, dict):
        return {k: _sanitize_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json(v) for v in obj]
    return obj


@dataclass
class CaseResult:
    """单条评测结果"""
    case_id: str
    question: str
    category: str
    difficulty: str
    answer: str
    # 各维度分数（0-10）
    rule_score: float = 0.0
    ragas_faithfulness: float = 0.0
    ragas_context_precision: float = 0.0
    ragas_context_recall: float = 0.0
    hallucination_score: float = 0.0
    citation_score: float = 0.0
    consistency_score: float = 0.0
    judge_score: float = 0.0
    overall_score: float = 0.0
    passed: bool = True
    fail_reasons: List[str] = field(default_factory=list)
    details: Dict = field(default_factory=dict)


@dataclass
class EvalReport:
    """评测报告"""
    eval_id: str
    timestamp: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    avg_score: float
    dimension_scores: Dict[str, float]       # 各维度平均分
    case_results: List[CaseResult]
    gate_passed: bool = False
    gate_reasons: List[str] = field(default_factory=list)
    config: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return _sanitize_json({
            "eval_id": self.eval_id,
            "timestamp": self.timestamp,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "avg_score": self.avg_score,
            "dimension_scores": self.dimension_scores,
            "gate_passed": self.gate_passed,
            "gate_reasons": self.gate_reasons,
            "config": self.config,
            "case_results": [
                {
                    "case_id": r.case_id,
                    "question": r.question,
                    "category": r.category,
                    "difficulty": r.difficulty,
                    "answer": r.answer[:200],
                    "overall_score": r.overall_score,
                    "passed": r.passed,
                    "fail_reasons": r.fail_reasons,
                    "scores": {
                        "rule": r.rule_score,
                        "ragas_faithfulness": r.ragas_faithfulness,
                        "ragas_context_precision": r.ragas_context_precision,
                        "ragas_context_recall": r.ragas_context_recall,
                        "hallucination": r.hallucination_score,
                        "citation": r.citation_score,
                        "consistency": r.consistency_score,
                        "judge": r.judge_score,
                    },
                }
                for r in self.case_results
            ],
        })

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"评测报告已保存: {path}")

    async def save_to_db(self):
        """保存报告到 PostgreSQL"""
        from ..core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO eval_reports (eval_id, data, avg_score, gate_passed, total_cases, passed_cases)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (eval_id) DO UPDATE SET
                   data=$2, avg_score=$3, gate_passed=$4, total_cases=$5, passed_cases=$6""",
                self.eval_id, json.dumps(self.to_dict(), ensure_ascii=False),
                self.avg_score, self.gate_passed, self.total_cases, self.passed_cases
            )
        logger.info(f"评测报告已保存到数据库: {self.eval_id}")

    @classmethod
    def from_dict(cls, data: Dict) -> "EvalReport":
        """从字典构造 EvalReport"""
        case_results = [
            CaseResult(
                case_id=r.get("case_id", ""),
                question=r.get("question", ""),
                category=r.get("category", ""),
                difficulty=r.get("difficulty", ""),
                answer=r.get("answer", ""),
                overall_score=r.get("overall_score", 0),
                passed=r.get("passed", True),
                fail_reasons=r.get("fail_reasons", []),
                rule_score=r.get("scores", {}).get("rule", 0),
                ragas_faithfulness=r.get("scores", {}).get("ragas_faithfulness", 0),
                ragas_context_precision=r.get("scores", {}).get("ragas_context_precision", 0),
                ragas_context_recall=r.get("scores", {}).get("ragas_context_recall", 0),
                hallucination_score=r.get("scores", {}).get("hallucination", 0),
                citation_score=r.get("scores", {}).get("citation", 0),
                consistency_score=r.get("scores", {}).get("consistency", 0),
                judge_score=r.get("scores", {}).get("judge", 0),
            )
            for r in data.get("case_results", [])
        ]
        return cls(
            eval_id=data.get("eval_id", ""),
            timestamp=data.get("timestamp", ""),
            total_cases=data.get("total_cases", 0),
            passed_cases=data.get("passed_cases", 0),
            failed_cases=data.get("failed_cases", 0),
            avg_score=data.get("avg_score", 0),
            dimension_scores=data.get("dimension_scores", {}),
            case_results=case_results,
            gate_passed=data.get("gate_passed", False),
            gate_reasons=data.get("gate_reasons", []),
            config=data.get("config", {}),
        )

    @staticmethod
    async def load_from_db(eval_id: str) -> Optional["EvalReport"]:
        """从 PostgreSQL 加载报告"""
        from ..core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT data FROM eval_reports WHERE eval_id=$1", eval_id
            )
        if not row:
            return None
        return EvalReport.from_dict(json.loads(row['data']))

    @staticmethod
    async def list_from_db(limit: int = 20) -> List[Dict]:
        """从 PostgreSQL 列出报告摘要"""
        from ..core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT eval_id, avg_score, gate_passed, total_cases, passed_cases, created_at
                   FROM eval_reports ORDER BY created_at DESC LIMIT $1""",
                limit
            )
        return [
            {
                "eval_id": r["eval_id"],
                "avg_score": r["avg_score"],
                "gate_passed": r["gate_passed"],
                "total_cases": r["total_cases"],
                "passed_cases": r["passed_cases"],
                "timestamp": r["created_at"].isoformat() if r["created_at"] else "",
            }
            for r in rows
        ]

    def print_summary(self):
        """打印终端摘要"""
        print("\n" + "=" * 80)
        print(f"  评测报告: {self.eval_id}")
        print(f"  时间: {self.timestamp}")
        print("=" * 80)

        # 维度平均分
        print(f"\n  维度平均分:")
        for dim, score in self.dimension_scores.items():
            bar = "█" * int(score) + "░" * (10 - int(score))
            print(f"    {dim:25s} {bar} {score:.1f}/10")

        # 每条 case 结果
        print(f"\n  评测用例:")
        print(f"  {'ID':20s} {'类别':12s} {'难度':6s} {'总分':6s} {'幻觉':6s} {'引用':6s} {'RAGAS':6s} {'状态':6s}")
        print("  " + "-" * 76)
        for r in self.case_results:
            status = "PASS" if r.passed else "FAIL"
            status_color = "" if r.passed else " <<<"
            print(f"  {r.case_id:20s} {r.category:12s} {r.difficulty:6s} "
                  f"{r.overall_score:5.1f} {r.hallucination_score:5.1f} {r.citation_score:5.1f} "
                  f"{r.ragas_faithfulness:5.2f} {status}{status_color}")

        # 门禁结果
        print(f"\n  {'=' * 40}")
        print(f"  总分: {self.avg_score:.1f}/10 | 通过: {self.passed_cases}/{self.total_cases} | "
              f"门禁: {'PASS' if self.gate_passed else 'FAIL'}")
        if self.gate_reasons:
            for reason in self.gate_reasons:
                print(f"  - {reason}")
        print("=" * 80 + "\n")


def generate_report(case_results: List[CaseResult], config: Dict = None) -> EvalReport:
    """从 case 结果列表生成评测报告"""
    eval_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    timestamp = datetime.now().isoformat()

    total = len(case_results)
    passed = sum(1 for r in case_results if r.passed)
    failed = total - passed
    avg_score = sum(r.overall_score for r in case_results) / total if total > 0 else 0

    # 各维度平均分
    dimensions = [
        "rule_score", "ragas_faithfulness", "ragas_context_precision",
        "ragas_context_recall",
        "hallucination_score", "citation_score", "consistency_score", "judge_score",
    ]
    dimension_scores = {}
    for dim in dimensions:
        vals = [getattr(r, dim, 0) for r in case_results]
        dimension_scores[dim] = round(sum(vals) / len(vals), 2) if vals else 0

    return EvalReport(
        eval_id=eval_id,
        timestamp=timestamp,
        total_cases=total,
        passed_cases=passed,
        failed_cases=failed,
        avg_score=round(avg_score, 2),
        dimension_scores=dimension_scores,
        case_results=case_results,
        config=config or {},
    )


def compare_reports(current: EvalReport, baseline: EvalReport) -> Dict:
    """对比两次评测报告，检测回归"""
    regressions = []
    improvements = []

    baseline_map = {r.case_id: r for r in baseline.case_results}

    for r in current.case_results:
        if r.case_id in baseline_map:
            prev = baseline_map[r.case_id]
            diff = r.overall_score - prev.overall_score
            if diff < -0.5:
                regressions.append({
                    "case_id": r.case_id,
                    "prev_score": prev.overall_score,
                    "curr_score": r.overall_score,
                    "diff": round(diff, 2),
                })
            elif diff > 0.5:
                improvements.append({
                    "case_id": r.case_id,
                    "prev_score": prev.overall_score,
                    "curr_score": r.overall_score,
                    "diff": round(diff, 2),
                })

    return {
        "regressions": regressions,
        "improvements": improvements,
        "score_change": round(current.avg_score - baseline.avg_score, 2),
    }
