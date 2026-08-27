"""
自定义 RAGAS 指标 - 反幻觉、引用准确性、多轮一致性
"""

from typing import List, Dict, Optional
import re
import logging

logger = logging.getLogger(__name__)


class HallucinationFree:
    """反幻觉指标 - 检查答案是否编造了不存在的信息"""

    name = "hallucination_free"

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def score(self, question: str, answer: str, contexts: List[str],
                    expected_not_contains: List[str] = None) -> Dict:
        scores = {}
        reasons = []
        failed = False
        fail_reason = ""

        # 规则检查：expected_not_contains
        if expected_not_contains:
            forbidden_found = []
            for forbidden in expected_not_contains:
                if forbidden in answer:
                    forbidden_found.append(forbidden)
            if forbidden_found:
                scores["forbidden_check"] = 0.0
                reasons.append(f"答案包含禁止内容: {forbidden_found}")
            else:
                scores["forbidden_check"] = 10.0

        # LLM 检查：逐条声明验证
        if self.llm and contexts:
            context_text = "\n".join(contexts[:5])
            prompt = f"""请判断以下回答中是否有编造的信息。

## 参考资料
{context_text}

## 问题
{question}

## 回答
{answer}

## 要求
检查回答中的事实声明，判断是否存在幻觉（编造信息）。
评分标准：
- 10分：所有事实声明都有参考资料支撑，或属于通用知识
- 7-9分：大部分事实有支撑，个别细节可能不够精确
- 4-6分：部分事实有支撑，但存在一些未验证的声明
- 1-3分：大量声明缺乏支撑或与参考资料矛盾
- 0分：完全编造，与参考资料严重矛盾

注意：
1. 如果参考资料不足以验证某个声明，但该声明属于常识或通用知识，不应视为幻觉
2. 如果回答明确表示"无法确定"或"根据现有资料无法判断"，这是诚实的回答，应给高分
3. 只有明确编造或与参考资料矛盾的信息才应被视为幻觉

返回 JSON 格式：
{{
    "hallucination_score": 0-10分（10=完全无幻觉，0=严重幻觉）,
    "hallucinated_claims": ["编造的声明1", "编造的声明2"],
    "supported_claims": ["有支撑的声明1", "有支撑的声明2"],
    "reasoning": "评分理由"
}}"""

            try:
                result = await self.llm.generate(prompt, temperature=0.1)
                import json
                json_match = re.search(r'\{[\s\S]*\}', result["text"])
                if json_match:
                    data = json.loads(json_match.group())
                    if "hallucination_score" in data:
                        scores["llm_hallucination"] = data["hallucination_score"]
                    else:
                        failed = True
                        fail_reason = "LLM返回JSON缺少hallucination_score字段"
                    if data.get("hallucinated_claims"):
                        reasons.append(f"编造的声明: {data['hallucinated_claims']}")
                else:
                    failed = True
                    fail_reason = "LLM返回内容不含JSON"
            except Exception as e:
                logger.warning(f"Hallucination LLM check failed: {e}")
                # 中性分5仅作占位显示；runner 会将失败指标排除出加权总分
                scores["llm_hallucination"] = 5.0
                failed = True
                fail_reason = f"LLM幻觉检查调用失败: {e}"

        # 综合分数
        if scores:
            avg_score = sum(scores.values()) / len(scores)
        else:
            avg_score = 10.0  # 无检查条件时默认满分

        return {
            "score": round(avg_score, 2),
            "details": scores,
            "reasons": reasons,
            "failed": failed,
            "fail_reason": fail_reason,
        }


class CitationAccuracy:
    """引用准确性指标 - 检查引用来源是否正确"""

    name = "citation_accuracy"

    def score(self, answer: str, citations: List[Dict], expected_source: str = "") -> Dict:
        scores = {}
        reasons = []

        # 检查是否有引用
        citation_pattern = r'\[来源\d+[:\s]([^\]]+)\]'
        cited_sources = re.findall(citation_pattern, answer)

        if not cited_sources and not citations:
            if expected_source:
                scores["has_citation"] = 0.0
                reasons.append(f"期望引用 {expected_source}，但答案无引用")
            else:
                scores["has_citation"] = 10.0  # 不需要引用时满分
        else:
            scores["has_citation"] = 10.0 if cited_sources or citations else 0.0

        # 检查期望来源是否被引用
        if expected_source:
            source_found = False
            for src in cited_sources:
                if expected_source in src:
                    source_found = True
                    break
            if not source_found:
                for c in citations:
                    csrc = c.get("source_file", "")
                    if expected_source in csrc:
                        source_found = True
                        break
            scores["expected_source"] = 10.0 if source_found else 0.0
            if not source_found:
                reasons.append(f"期望引用 {expected_source}，实际引用: {cited_sources}")

        # 综合分数
        avg_score = sum(scores.values()) / len(scores) if scores else 10.0
        return {
            "score": round(avg_score, 2),
            "details": scores,
            "reasons": reasons,
        }


class MultiTurnConsistency:
    """多轮一致性指标 - 检查前后回答是否矛盾"""

    name = "multi_turn_consistency"

    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def score(self, question: str, answer: str,
                    previous_qa_pairs: List[Dict] = None) -> Dict:
        if not previous_qa_pairs or not self.llm:
            return {"score": 10.0, "details": {}, "reasons": [], "failed": False, "fail_reason": ""}

        history_text = ""
        for i, qa in enumerate(previous_qa_pairs, 1):
            history_text += f"轮次{i} - 问题: {qa.get('question', '')}\n轮次{i} - 回答: {qa.get('answer', '')}\n\n"

        prompt = f"""请检查以下多轮对话中是否存在前后矛盾。

## 历史对话
{history_text}

## 当前问题
{question}

## 当前回答
{answer}

## 要求
检查当前回答是否与历史对话中的信息矛盾。
返回 JSON 格式：
{{
    "consistency_score": 0-10分（10=完全一致，0=严重矛盾）,
    "contradictions": ["矛盾点1", "矛盾点2"],
    "reasoning": "评分理由"
}}"""

        try:
            result = await self.llm.generate(prompt, temperature=0.1)
            import json
            json_match = re.search(r'\{[\s\S]*\}', result["text"])
            if json_match:
                data = json.loads(json_match.group())
                if "consistency_score" in data:
                    score = data["consistency_score"]
                    reasons = []
                    if data.get("contradictions"):
                        reasons.append(f"矛盾: {data['contradictions']}")
                    return {
                        "score": score,
                        "details": {"consistency": score},
                        "reasons": reasons,
                        "failed": False,
                        "fail_reason": "",
                    }
                # JSON 缺少分数字段：显式标记失败，不再静默给满分
                return {
                    "score": 10.0,
                    "details": {},
                    "reasons": [],
                    "failed": True,
                    "fail_reason": "LLM返回JSON缺少consistency_score字段",
                }
        except Exception as e:
            logger.warning(f"MultiTurnConsistency check failed: {e}")
            return {
                "score": 10.0,
                "details": {},
                "reasons": [],
                "failed": True,
                "fail_reason": f"多轮一致性LLM检查失败: {e}",
            }

        return {"score": 10.0, "details": {}, "reasons": [], "failed": True, "fail_reason": "LLM返回内容不含JSON"}
