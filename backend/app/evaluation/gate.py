"""
评测门禁 - 决定是否允许发布
"""

from dataclasses import dataclass
from typing import Tuple, List
import logging

from .report import EvalReport

logger = logging.getLogger(__name__)


@dataclass
class GateConfig:
    """门禁配置"""
    min_overall_score: float = 7.0         # 总分阈值（0-10）
    min_hallucination_score: float = 7.0   # 反幻觉阈值
    min_citation_score: float = 6.0        # 引用准确性阈值
    max_single_fail: int = 0               # 允许的单条失败数
    regression_threshold: float = -0.5     # 回归阈值（比上次下降超过此值）


DEFAULT_GATE = GateConfig()


class EvalGate:
    """评测门禁"""

    def __init__(self, config: GateConfig = None):
        self.config = config or DEFAULT_GATE

    def check(self, report: EvalReport, baseline: EvalReport = None) -> Tuple[bool, List[str]]:
        """
        检查报告是否通过门禁

        Returns:
            (passed, reasons) - 是否通过，未通过原因列表
        """
        reasons = []
        cfg = self.config

        # 1. 总分检查
        if report.avg_score < cfg.min_overall_score:
            reasons.append(
                f"总分 {report.avg_score:.1f} < {cfg.min_overall_score}"
            )

        # 2. 幻觉分数检查
        avg_hallucination = report.dimension_scores.get("hallucination_score", 0)
        if avg_hallucination < cfg.min_hallucination_score:
            reasons.append(
                f"幻觉平均分 {avg_hallucination:.1f} < {cfg.min_hallucination_score}"
            )

        # 3. 引用准确性检查
        avg_citation = report.dimension_scores.get("citation_score", 0)
        if avg_citation < cfg.min_citation_score:
            reasons.append(
                f"引用准确性 {avg_citation:.1f} < {cfg.min_citation_score}"
            )

        # 4. 单条失败数检查
        failed_count = report.failed_cases
        if failed_count > cfg.max_single_fail:
            reasons.append(
                f"失败用例数 {failed_count} > 允许值 {cfg.max_single_fail}"
            )

        # 5. 回归检查（与基线对比）
        if baseline:
            from .report import compare_reports
            comparison = compare_reports(report, baseline)
            if comparison["regressions"]:
                for reg in comparison["regressions"]:
                    reasons.append(
                        f"回归: {reg['case_id']} 分数从 {reg['prev_score']:.1f} "
                        f"降至 {reg['curr_score']:.1f} ({reg['diff']:+.1f})"
                    )

        passed = len(reasons) == 0
        if passed:
            logger.info("评测门禁: PASS")
        else:
            logger.warning(f"评测门禁: FAIL - {'; '.join(reasons)}")

        return passed, reasons
