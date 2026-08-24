"""
LLM-as-Judge评估器 - 接入项目 LLM，多维度打分
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import json
import re
import logging

logger = logging.getLogger(__name__)


@dataclass
class JudgeScore:
    dimension: str
    score: int
    reasoning: str
    evidence: str = ""


@dataclass
class JudgeResult:
    judge_id: str
    timestamp: str
    question: str
    answer: str
    reference: str
    scores: List[JudgeScore]
    overall_score: float
    feedback: str


class LLMJudge:
    """LLM-as-Judge评估器 - 6维度评分"""

    def __init__(self, config: Dict = None, llm_client=None):
        self.config = config or {}
        self.dimensions = ["accuracy", "completeness", "relevance", "clarity", "citation", "hallucination"]
        self.llm = llm_client
        self.weights = {
            "accuracy": 1.5,
            "completeness": 1.2,
            "relevance": 1.0,
            "clarity": 0.8,
            "citation": 1.0,
            "hallucination": 1.5,  # 幻觉检测权重最高
        }

    async def initialize(self, llm_client):
        self.llm = llm_client

    async def judge(self, question: str, answer: str, reference: str = "",
                    contexts: List[str] = None) -> JudgeResult:
        if not self.llm:
            raise ValueError("LLM client not initialized")

        prompt = self._build_judge_prompt(question, answer, reference, contexts or [])
        result = await self.llm.generate(prompt, temperature=0.1)
        scores = self._parse_scores(result["text"])
        overall = self._calculate_overall(scores)
        feedback = self._generate_feedback(scores, overall)

        return JudgeResult(
            judge_id=f"judge_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            timestamp=datetime.now().isoformat(),
            question=question, answer=answer, reference=reference,
            scores=scores, overall_score=overall, feedback=feedback,
        )

    async def batch_judge(self, samples: List[Dict], concurrency: int = 3) -> List[JudgeResult]:
        import asyncio
        semaphore = asyncio.Semaphore(concurrency)

        async def judge_one(sample):
            async with semaphore:
                return await self.judge(
                    sample["question"], sample["answer"],
                    sample.get("reference", ""), sample.get("contexts", [])
                )

        return await asyncio.gather(*[judge_one(s) for s in samples])

    def _build_judge_prompt(self, question: str, answer: str, reference: str,
                            contexts: List[str]) -> str:
        context_text = "\n".join(contexts[:5]) if contexts else "无"
        return f"""请评估以下回答的质量。你是一个严格的评审专家。

## 问题
{question}

## 参考答案
{reference or "无"}

## 参考资料
{context_text}

## 待评估回答
{answer}

## 评分维度（每项 1-10 分）

1. **accuracy（准确性）**: 答案中的事实是否正确
2. **completeness（完整性）**: 是否完整回答了问题
3. **relevance（相关性）**: 回答是否切题
4. **clarity（清晰性）**: 表述是否清晰易懂
5. **citation（引用准确性）**: 引用来源是否正确，是否标注了来源
6. **hallucination（反幻觉）**: 是否编造了参考资料中不存在的信息（10=完全无幻觉，0=严重幻觉）

## 输出格式
严格返回 JSON，不要有其他文字：
{{
    "accuracy": {{"score": 分数, "reasoning": "理由"}},
    "completeness": {{"score": 分数, "reasoning": "理由"}},
    "relevance": {{"score": 分数, "reasoning": "理由"}},
    "clarity": {{"score": 分数, "reasoning": "理由"}},
    "citation": {{"score": 分数, "reasoning": "理由"}},
    "hallucination": {{"score": 分数, "reasoning": "理由"}}
}}"""

    def _parse_scores(self, response: str) -> List[JudgeScore]:
        scores = []
        try:
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                for dim in self.dimensions:
                    if dim in data:
                        d = data[dim]
                        if isinstance(d, dict):
                            scores.append(JudgeScore(
                                dimension=dim,
                                score=d.get("score", 5),
                                reasoning=d.get("reasoning", ""),
                            ))
                        elif isinstance(d, (int, float)):
                            scores.append(JudgeScore(dimension=dim, score=int(d), reasoning=""))
        except Exception as e:
            logger.warning(f"Failed to parse judge scores: {e}")
            for dim in self.dimensions:
                scores.append(JudgeScore(dimension=dim, score=5, reasoning="解析失败"))
        return scores

    def _calculate_overall(self, scores: List[JudgeScore]) -> float:
        if not scores:
            return 0
        total_weight = sum(self.weights.get(s.dimension, 1.0) for s in scores)
        weighted_sum = sum(s.score * self.weights.get(s.dimension, 1.0) for s in scores)
        return round(weighted_sum / total_weight, 2) if total_weight > 0 else 0

    def _generate_feedback(self, scores: List[JudgeScore], overall: float) -> str:
        if overall >= 8:
            return "回答质量优秀"
        elif overall >= 6:
            return "回答质量良好，有改进空间"
        elif overall >= 4:
            return "回答质量一般，建议优化"
        return "回答质量较差，需要大幅改进"
