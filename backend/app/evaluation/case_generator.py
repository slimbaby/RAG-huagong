"""
评测用例自动生成器 - 从文档内容生成评测 Q&A 对
"""

from typing import Dict, List, Optional
import json
import re
import logging

from .dataset import EvalCase

logger = logging.getLogger(__name__)

GENERATE_PROMPT = """你是一个评测用例生成专家。请根据以下文档内容，生成 {max_cases} 个评测问答对。

## 文档信息
文件名: {filename}

## 文档内容
{content}

## 要求
1. 从事实性内容中提取问题（如数据、人名、数字、日期、流程步骤等）
2. 问题应该是用户真实会问的查询
3. 答案必须在文档内容中有明确依据，不要编造
4. 每个问答对包含：question、ground_truth（精确答案）、expected_contains（答案中应出现的关键词列表）
5. 优先覆盖不同类型：事实查询、表格数据查询、流程查询

## 输出格式
严格返回 JSON 数组，不要有其他文字：
[
    {{
        "question": "问题内容",
        "ground_truth": "精确答案",
        "expected_contains": ["关键词1", "关键词2"],
        "category": "单文档查询"
    }}
]"""


class EvalCaseGenerator:
    """从文档 chunks 自动生成评测用例"""

    def __init__(self, llm_client):
        self.llm = llm_client

    async def generate_from_chunks(
        self,
        doc_id: str,
        filename: str,
        chunks: List[Dict],
        max_cases: int = 10,
        scope_ids: List[str] = None,
    ) -> List[EvalCase]:
        """从文档 chunks 自动生成评测用例。scope_ids 写入每条 case 的 metadata 便于分层评测"""
        self._case_metadata = {"scope_ids": scope_ids or []}
        if not self.llm or not chunks:
            return []

        # 提取 chunk 内容，限制总长度
        content_parts = []
        total_len = 0
        max_content_len = 8000  # 限制送入 LLM 的内容长度
        for chunk in chunks:
            text = ""
            if isinstance(chunk, dict):
                text = chunk.get("content", "")
                if not text and "metadata" in chunk:
                    text = chunk["metadata"].get("content", "")
            else:
                text = getattr(chunk, "content", "")

            text = text.strip()
            if not text or len(text) < 20:
                continue
            if total_len + len(text) > max_content_len:
                break
            content_parts.append(text)
            total_len += len(text)

        if not content_parts:
            logger.warning(f"文档 {filename} 无有效内容用于生成评测用例")
            return []

        content_text = "\n---\n".join(content_parts)

        # 调用 LLM 生成
        prompt = GENERATE_PROMPT.format(
            max_cases=max_cases,
            filename=filename,
            content=content_text,
        )

        try:
            result = await self.llm.generate(prompt, temperature=0.3)
            response_text = result.get("text", "")
            cases = self._parse_response(doc_id, filename, response_text)
            logger.info(f"为文档 {filename} 生成了 {len(cases)} 条评测用例")
            return cases
        except Exception as e:
            logger.error(f"评测用例生成失败: {e}")
            return []

    def _parse_response(self, doc_id: str, filename: str, response: str) -> List[EvalCase]:
        """解析 LLM 返回的 JSON，构造 EvalCase 列表"""
        # 提取 JSON 数组
        json_match = re.search(r'\[[\s\S]*\]', response)
        if not json_match:
            logger.warning("LLM 返回中未找到 JSON 数组")
            return []

        try:
            items = json.loads(json_match.group())
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")
            return []

        cases = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            question = item.get("question", "").strip()
            ground_truth = item.get("ground_truth", "").strip()
            if not question:
                continue

            case_id = f"doc_{doc_id}_{i}"
            case = EvalCase(
                id=case_id,
                question=question,
                category=item.get("category", "单文档查询"),
                difficulty="medium",
                ground_truth=ground_truth,
                expected_contains=item.get("expected_contains", []),
                expected_source=filename,
                source_doc_id=doc_id,
                layer="document",
                stale=False,
                metadata=self._case_metadata,
            )
            cases.append(case)

        return cases
