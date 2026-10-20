"""
评测结果反思归因 —— 把低分 case 蒸馏成系统级教训

流程：EvalReport → 筛失败 case → 批量归因（找模式）→ 单条蒸馏（补漏）
     → 写入 LessonStore(SYSTEM_SCOPE)

设计要点：
- 批量归因优先：能发现"10 个失败 case 里 8 个都是召回问题"这类模式，
  单条蒸馏看不到这个。样本不足 3 条时降级为单条蒸馏。
- 单条蒸馏置信度低于批量归因（0.6 vs 0.7），因为缺少交叉验证。
- 全流程不抛异常：反思链路失败不影响评测主流程。
"""

import json
import logging
import re
from typing import Dict, List

from ..prompts.lesson import (
    LESSON_ATTRIBUTION_PROMPT as _ATTRIBUTION_PROMPT,
    LESSON_FROM_CASE_PROMPT as _FROM_CASE_PROMPT,
)
from ..services.lesson_store import SYSTEM_SCOPE, LessonStore

logger = logging.getLogger(__name__)


def _get_score(case, dim: str) -> float:
    """从 CaseResult 取分数。

    注意：CaseResult 的真实字段名带 _score 后缀（judge_score / citation_score），
    映射表必须与 app/evaluation/report.py 的 CaseResult 定义严格一致，
    否则 getattr 取不到会静默返回 0.0，导致所有 case 被误判为该维度失败。
    """
    mapping = {
        "ragas_context_recall": "ragas_context_recall",
        "ragas_faithfulness": "ragas_faithfulness",
        "ragas_context_precision": "ragas_context_precision",
        "judge_score": "judge_score",
        "citation_score": "citation_score",
    }
    attr = mapping.get(dim, dim)
    value = getattr(case, attr, None)
    if value is None:
        value = 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class EvalReflector:
    """评测归因器：把评测失败样本转成可复用的系统级教训"""

    # 各维度分数低于此值视为该维度失败
    # 取值字段必须与 app/evaluation/report.py 的 CaseResult 定义一致：
    #   ragas_context_recall / ragas_faithfulness / citation_score / judge_score
    # 注意：CaseResult 没有独立的 completeness 字段，
    # judge_score 是 LLM-as-Judge 六维加权总分（含 completeness/accuracy/clarity 等），
    # 这里用它代表"回答整体质量"，归因标签写"质量"而非"完整"更准确。
    DIMENSION_THRESHOLDS = {
        "ragas_context_recall": 6.0,      # 召回
        "ragas_faithfulness": 6.0,        # 忠实
        "judge_score": 6.0,                # 回答质量（judge 综合）
        "citation_score": 6.0,            # 引用
    }

    # 维度 → lesson 维度标签（面向 LLM 可读的分类）
    DIMENSION_LABELS = {
        "ragas_context_recall": "召回",
        "ragas_faithfulness": "忠实",
        "judge_score": "质量",
        "citation_score": "引用",
    }

    def __init__(self, lesson_store: LessonStore, llm_client=None):
        self.store = lesson_store
        self.llm = llm_client

    async def reflect_report(self, report, top_n: int = 20) -> List[Dict]:
        """从评测报告反思出系统级教训"""
        try:
            failed = [c for c in report.case_results if not c.passed]
        except AttributeError:
            logger.warning("[reflector] 报告对象缺少 case_results，跳过")
            return []

        if not failed:
            logger.info("[reflector] 无失败 case，跳过归因")
            return []

        # 只取最差的一批，低分样本的规律才有统计意义
        candidates = sorted(failed, key=lambda c: c.overall_score)[:top_n]
        if not candidates:
            return []

        logger.info(f"[reflector] 失败 {len(failed)} 条，取最低分 {len(candidates)} 条归因")

        # 批量归因优先，失败降级为单条蒸馏
        lessons = await self._batch_attribute(candidates)
        if not lessons:
            lessons = await self._single_distill(candidates)

        # 写入 LessonStore（系统级 scope）
        written = []
        for item in lessons:
            content = (item.get("lesson") or "").strip()
            if not content:
                continue
            lesson = self.store.add(
                scope=SYSTEM_SCOPE,
                content=content,
                dimension=item.get("dimension", "通用"),
                action=item.get("action", ""),
                importance=0.85,           # 系统级：经过评测集验证
                confidence=item.get("confidence", 0.7),
                source="eval_attribution",
            )
            if lesson:
                written.append(item)

        logger.info(f"[reflector] 产出 {len(written)} 条系统级教训")
        return written

    def _bucket_by_dimension(self, failed: List) -> Dict[str, List]:
        """按失败维度分桶，每条 case 只归入第一个失败维度（避免重复统计）"""
        buckets: Dict[str, List] = {}
        for c in failed:
            for dim, threshold in self.DIMENSION_THRESHOLDS.items():
                if _get_score(c, dim) < threshold:
                    buckets.setdefault(dim, []).append(c)
                    break
        return buckets

    async def _batch_attribute(self, cases: List) -> List[Dict]:
        """批量归因——能发现跨 case 的模式"""
        if not self.llm or len(cases) < 3:
            return []

        buckets = self._bucket_by_dimension(cases)
        if not buckets:
            return []

        case_lines = []
        for dim, items in buckets.items():
            label = self.DIMENSION_LABELS.get(dim, dim)
            case_lines.append(f"### {label}维度失败（{len(items)} 条）")
            for c in items[:8]:
                case_lines.append(
                    f"- 问题：{c.question}\n"
                    f"  得分：{c.overall_score:.1f} "
                    f"(召回{c.ragas_context_recall:.1f}/忠实{c.ragas_faithfulness:.1f}"
                    f"/质量{c.judge_score:.1f}/引用{c.citation_score:.1f})\n"
                    f"  失败原因：{', '.join(c.fail_reasons) or '综合分偏低'}"
                )
            case_lines.append("")

        prompt = _ATTRIBUTION_PROMPT.format(cases="\n".join(case_lines))
        try:
            result = await self.llm.generate(prompt, temperature=0.1)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            return self._parse_json_array(text)
        except Exception as e:
            logger.error(f"[reflector] 批量归因失败: {e}")
            return []

    async def _single_distill(self, cases: List) -> List[Dict]:
        """单条蒸馏——批量失败时的兜底，粒度细但看不到模式"""
        if not self.llm:
            return []

        out = []
        for c in cases[:10]:
            prompt = _FROM_CASE_PROMPT.format(
                question=c.question,
                reference=(c.details or {}).get("reference", ""),
                answer=(c.answer or "")[:500],
                scores=str({k: v for k, v in (c.details or {}).items() if k != "reference"}),
                fail_reasons=", ".join(c.fail_reasons) or "综合分偏低",
            )
            try:
                result = await self.llm.generate(prompt, temperature=0.1)
                text = (result.get("text", "") if isinstance(result, dict) else str(result)).strip()
                if text and len(text) > 8:
                    out.append({
                        "dimension": self._guess_dimension(c),
                        "action": "",
                        "lesson": text,
                        "confidence": 0.6,   # 单条蒸馏缺少交叉验证，置信度低于批量
                    })
            except Exception as e:
                logger.error(f"[reflector] 单条蒸馏失败: {e}")
        return out

    def _guess_dimension(self, case) -> str:
        """猜该 case 的主要失败维度"""
        for dim, threshold in self.DIMENSION_THRESHOLDS.items():
            if _get_score(case, dim) < threshold:
                return self.DIMENSION_LABELS.get(dim, "通用")
        return "通用"

    def _parse_json_array(self, text: str) -> List[Dict]:
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group())
        except json.JSONDecodeError:
            logger.warning(f"[reflector] 归因结果 JSON 解析失败: {text[:200]}")
            return []
        return [d for d in data if isinstance(d, dict) and d.get("lesson")]
