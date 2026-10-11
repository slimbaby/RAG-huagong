"""
记忆提取器 - 从对话中提取值得长期记住的关键信息
"""

import json
import logging
import re
from typing import Dict, List

from ..prompts.memory import (
    MEMORY_EXTRACT_PROMPT as _EXTRACT_PROMPT,
    MEMORY_MERGE_PROMPT as _MERGE_PROMPT,
)

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """从对话中提取值得记住的信息"""

    # 模板已外部化到 app/prompts/memory.py；保留类属性引用兼容既有访问路径
    EXTRACT_PROMPT = _EXTRACT_PROMPT
    MERGE_PROMPT = _MERGE_PROMPT

    def __init__(self, llm_client):
        self.llm = llm_client

    async def extract(self, user_query: str, answer: str) -> List[Dict]:
        """从一轮对话中提取关键信息"""
        if not self.llm:
            return []

        conversation = f"用户: {user_query}\n助手: {answer}"
        prompt = self.EXTRACT_PROMPT.format(conversation=conversation)

        try:
            result = await self.llm.generate(prompt)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            memories = self._parse_response(text)
            logger.info(f"[memory_extractor] 提取到 {len(memories)} 条记忆")
            return memories
        except Exception as e:
            logger.error(f"记忆提取失败: {e}")
            return []

    async def merge_memories(self, memory_a: str, memory_b: str) -> str:
        """合并两条相似记忆"""
        if not self.llm:
            return memory_a  # 保留第一条

        prompt = self.MERGE_PROMPT.format(memory_a=memory_a, memory_b=memory_b)
        try:
            result = await self.llm.generate(prompt)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            return text.strip()[:200]
        except Exception as e:
            logger.error(f"记忆合并失败: {e}")
            return memory_a

    def _parse_response(self, text: str) -> List[Dict]:
        """解析 LLM 返回的 JSON 数组"""
        text = text.strip()
        # 尝试提取 JSON 数组
        json_match = re.search(r'\[.*\]', text, re.DOTALL)
        if not json_match:
            return []

        try:
            items = json.loads(json_match.group())
        except json.JSONDecodeError:
            logger.warning(f"记忆提取结果 JSON 解析失败: {text[:200]}")
            return []

        memories = []
        for item in items:
            if not isinstance(item, dict):
                continue
            content = item.get("content", "").strip()
            if not content or len(content) > 200:
                continue

            memory_type = item.get("type", "fact")
            if memory_type not in ("fact", "preference", "decision", "identity"):
                memory_type = "fact"

            importance = item.get("importance", 0.5)
            try:
                importance = float(importance)
                importance = max(0.0, min(1.0, importance))
            except (ValueError, TypeError):
                importance = 0.5

            memories.append({
                "content": content,
                "type": memory_type,
                "importance": importance,
            })

        return memories
