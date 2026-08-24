"""
记忆提取器 - 从对话中提取值得长期记住的关键信息
"""

import json
import logging
import re
from typing import Dict, List

logger = logging.getLogger(__name__)


class MemoryExtractor:
    """从对话中提取值得记住的信息"""

    EXTRACT_PROMPT = """分析以下对话，提取值得长期记住的关键信息。

## 提取规则
只提取以下类型的信息：
- 用户明确表达的偏好（如"我喜欢简洁的回答"、"请用表格展示"）
- 重要决策或结论（如"我们决定采用方案A"、"这个设备需要更换"）
- 关键事实（如"园区共有3个化工厂"、"反应釜温度上限是200度"）
- 用户身份信息（如"我是安全管理员"、"我在环保部门工作"）
- 用户提出的特定要求（如"回答时请引用法规编号"）

## 不要提取
- 通用问答内容（如"你好"、"谢谢"）
- 已被知识库文档完整覆盖的标准信息
- 临时性的操作指令（如"帮我查一下"、"再详细说说"）
- 过于模糊或无实质内容的对话

## 对话内容
{conversation}

## 输出格式
以 JSON 数组返回，每项包含：
- content: 记忆内容（一句话，不超过100字，必须是完整的可独立理解的陈述句）
- type: "fact" | "preference" | "decision" | "identity"
- importance: 0.0-1.0 的重要性分数（0.9+=关键身份/决策，0.6+=重要偏好/事实，0.3+=一般信息）

如果没有值得记住的信息，返回空数组 []
只返回 JSON 数组，不要任何解释。"""

    MERGE_PROMPT = """以下两条记忆内容相似，请合并为一条更准确、更完整的记忆。

记忆A: {memory_a}
记忆B: {memory_b}

合并要求：
1. 保留更具体、更完整的信息
2. 如果矛盾，以更新的为准
3. 输出一条合并后的记忆，不超过100字

只返回合并后的记忆文本，不要解释。"""

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
