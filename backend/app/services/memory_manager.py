"""
记忆管理器 - 三层记忆架构（短期/长期/工作记忆）
"""

import json
import hashlib
import math
import os
import time
import asyncio
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from ..prompts.memory import (
    SYSTEM_PROMPT_WITH_MEMORY as _SYS_PROMPT_KB,
    SYSTEM_PROMPT_WITH_MEMORY_GENERAL as _SYS_PROMPT_GENERAL,
    SYSTEM_PROMPT_WITH_MEMORY_BUSINESS as _SYS_PROMPT_BUSINESS,
)

logger = logging.getLogger(__name__)


@dataclass
class Memory:
    memory_id: str
    session_id: str
    content: str
    memory_type: str  # "fact" | "preference" | "decision" | "identity" | "summary"
    importance: float
    access_count: int = 0
    last_accessed: float = 0
    created_at: float = 0
    fingerprint: str = ""
    expired: bool = False
    vector_id: Optional[str] = None  # Milvus 中的向量 ID


class MemoryManager:
    """三层记忆管理器"""

    # system prompt 模板已外部化到 app/prompts/memory.py（集中管理）；
    # 保留类属性引用以兼容既有访问路径 MemoryManager.SYSTEM_PROMPT_WITH_MEMORY
    SYSTEM_PROMPT_WITH_MEMORY = _SYS_PROMPT_KB
    SYSTEM_PROMPT_WITH_MEMORY_GENERAL = _SYS_PROMPT_GENERAL
    SYSTEM_PROMPT_WITH_MEMORY_BUSINESS = _SYS_PROMPT_BUSINESS

    def __init__(self, session_store, memory_extractor, embedding_service,
                 llm_client, milvus_store=None, config=None):
        self.session_store = session_store
        self.extractor = memory_extractor
        self.embedding = embedding_service
        self.llm = llm_client
        self.milvus = milvus_store
        self.config = config
        self.storage_path = "./memories"
        self.collection_name = "conversation_memories"
        os.makedirs(self.storage_path, exist_ok=True)

        # 配置参数
        self.max_messages = getattr(config, "MEMORY_SHORT_TERM_MAX_MESSAGES", 60) if config else 60
        self.similarity_threshold = getattr(config, "MEMORY_LONG_TERM_SIMILARITY_THRESHOLD", 0.85) if config else 0.85
        self.expire_days = getattr(config, "MEMORY_LONG_TERM_EXPIRE_DAYS", 30) if config else 30
        self.min_importance = getattr(config, "MEMORY_LONG_TERM_MIN_IMPORTANCE", 0.3) if config else 0.3
        self.recall_top_k = getattr(config, "MEMORY_RECALL_TOP_K", 5) if config else 5
        self.token_budget = getattr(config, "MEMORY_TOKEN_BUDGET", 1000) if config else 1000

        # 内存中的记忆缓存
        self._memories: Dict[str, Memory] = {}
        self._memory_collection = None
        self._load_memories()

    # ============================================================
    # 短期记忆 (Session Store)
    # ============================================================

    async def save_turn(self, session_id: str, user_msg: str, assistant_msg: str,
                        citations: list = None):
        """保存一轮对话到 Redis"""
        await self.session_store.add_message(session_id, "user", user_msg)
        await self.session_store.add_message(session_id, "assistant", assistant_msg,
                                             citations=citations)

    async def get_chat_history(self, session_id: str, limit: int = 60) -> List[Dict]:
        """获取短期对话历史"""
        return await self.session_store.get_messages(session_id, limit=limit)

    # ============================================================
    # 长期记忆 (Memory Store)
    # ============================================================

    async def extract_and_store(self, session_id: str, user_query: str, answer: str):
        """从本轮对话中提取关键事实并存储到长期记忆"""
        if not self.extractor:
            return

        try:
            extracted = await self.extractor.extract(user_query, answer)
            if not extracted:
                return

            for item in extracted:
                await self._store_memory(
                    session_id=session_id,
                    content=item["content"],
                    memory_type=item["type"],
                    importance=item["importance"],
                )
        except Exception as e:
            logger.error(f"记忆提取和存储失败: {e}")

    async def _store_memory(self, session_id: str, content: str, memory_type: str,
                            importance: float):
        """存储单条记忆（含去重）"""
        fingerprint = hashlib.md5(content.encode()).hexdigest()[:16]

        # 检查精确重复
        for m in self._memories.values():
            if m.fingerprint == fingerprint and not m.expired:
                logger.debug(f"记忆精确重复，跳过: {content[:50]}")
                return

        # 向量去重
        if self.embedding and self.milvus:
            try:
                vector = await self.embedding.embed_query(content)
                similar = await self._search_similar_memories(vector, top_k=1)
                if similar and similar[0]["score"] >= self.similarity_threshold:
                    existing = self._memories.get(similar[0]["memory_id"])
                    if existing and not existing.expired:
                        # 合并：更新内容和重要性
                        if self.extractor:
                            merged = await self.extractor.merge_memories(existing.content, content)
                            existing.content = merged
                        existing.importance = max(existing.importance, importance)
                        existing.last_accessed = time.time()
                        self._persist_memories()
                        logger.info(f"记忆合并: {content[:50]} → {existing.memory_id}")
                        return
            except Exception as e:
                logger.warning(f"向量去重失败: {e}")

        # 创建新记忆
        memory = Memory(
            memory_id=str(uuid.uuid4())[:8],
            session_id=session_id,
            content=content,
            memory_type=memory_type,
            importance=importance,
            access_count=0,
            last_accessed=time.time(),
            created_at=time.time(),
            fingerprint=fingerprint,
        )

        # 写入 Milvus
        if self.embedding and self.milvus:
            try:
                vector = await self.embedding.embed_query(content)
                vector_id = await self._insert_memory_vector(memory.memory_id, vector)
                memory.vector_id = vector_id
            except Exception as e:
                logger.warning(f"记忆向量写入失败: {e}")

        self._memories[memory.memory_id] = memory
        self._persist_memories()
        logger.info(f"新记忆存储: [{memory_type}] {content[:50]} (id={memory.memory_id})")

    async def recall(self, query: str, session_id: str = None, top_k: int = None) -> List[Memory]:
        """根据 query 语义召回相关长期记忆"""
        top_k = top_k or self.recall_top_k

        if not self.embedding or not self.milvus:
            return []

        try:
            query_vector = await self.embedding.embed_query(query)
            similar = await self._search_similar_memories(query_vector, top_k=top_k * 2)

            memories = []
            for item in similar:
                memory = self._memories.get(item["memory_id"])
                if memory and not memory.expired:
                    memory.access_count += 1
                    memory.last_accessed = time.time()
                    memories.append(memory)

            # 按综合分数排序：相关性 × 时间衰减 × 重要性
            ranked = self._rank_memories(memories, [s["score"] for s in similar[:len(memories)]])

            self._persist_memories()
            return ranked[:top_k]

        except Exception as e:
            logger.error(f"记忆召回失败: {e}")
            return []

    def _rank_memories(self, memories: List[Memory], scores: List[float]) -> List[Memory]:
        """按 综合分数 = 相关性 × 时间衰减 × 重要性 排序"""
        now = time.time()
        for i, m in enumerate(memories):
            relevance = scores[i] if i < len(scores) else 0.0
            days_since = (now - m.last_accessed) / 86400
            time_decay = math.exp(-0.05 * days_since)
            m.rank_score = relevance * time_decay * m.importance
        return sorted(memories, key=lambda m: getattr(m, 'rank_score', 0), reverse=True)

    def _get_memory_collection(self, dim: int):
        """惰性获取/创建记忆专用 collection。同步阻塞，必须在 worker 线程中调用"""
        if self._memory_collection is not None:
            return self._memory_collection

        from pymilvus import Collection, FieldSchema, CollectionSchema, DataType, connections, utility

        connections.connect(alias="default", host=self.milvus.host, port=self.milvus.port)
        if utility.has_collection(self.collection_name):
            col = Collection(self.collection_name)
        else:
            fields = [
                FieldSchema("memory_id", DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema("vector", DataType.FLOAT_VECTOR, dim=dim),
            ]
            col = Collection(self.collection_name, CollectionSchema(fields))
            col.create_index("vector", {
                "index_type": "HNSW",
                "metric_type": "COSINE",
                "params": {"M": 16, "efConstruction": 128},
            })
            logger.info(f"已创建长期记忆collection: {self.collection_name} (dim={dim})")
        col.load()
        self._memory_collection = col
        return col

    async def _search_similar_memories(self, vector: List[float], top_k: int = 5) -> List[Dict]:
        """在 Milvus 中搜索相似记忆向量"""
        if not self.milvus:
            return []

        def _do():
            col = self._get_memory_collection(len(vector))
            results = col.search(
                data=[vector],
                anns_field="vector",
                param={"metric_type": "COSINE", "params": {"ef": 64}},
                limit=top_k,
                output_fields=["memory_id"],
            )
            out = []
            for hits in results:
                for hit in hits:
                    out.append({
                        "memory_id": hit.entity.get("memory_id"),
                        "score": float(hit.score),
                    })
            return out

        try:
            return await asyncio.to_thread(_do)
        except Exception as e:
            logger.warning(f"Milvus 记忆搜索失败: {e}")
            return []

    async def _insert_memory_vector(self, memory_id: str, vector: List[float]) -> str:
        """将记忆向量插入 Milvus"""

        def _do():
            col = self._get_memory_collection(len(vector))
            col.insert([[memory_id], [vector]])
            col.flush()

        await asyncio.to_thread(_do)
        logger.debug(f"记忆向量已写入: {memory_id} (dim={len(vector)})")
        return memory_id

    # ============================================================
    # 上下文构建 (Context Building)
    # ============================================================

    async def build_context(self, session_id: str, user_query: str,
                            kb_context: str = "", intent: str = "kb_query",
                            retrieval_confident: bool = True,
                            query_type: str = "factual") -> tuple:
        """构建完整的多轮对话 messages 数组，供 LLM.chat() 使用

        Returns:
            (messages, relevant_memories) — messages 供 LLM 使用，memories 供 tracing
        """
        # 1. 获取短期对话历史
        chat_history = await self.get_chat_history(session_id, limit=self.max_messages)

        # 2. 召回长期记忆
        relevant_memories = await self.recall(user_query, session_id=session_id)

        # 3. 格式化长期记忆
        memory_text = self._format_memories(relevant_memories)

        # 4. 格式化对话历史
        history_text = self._format_chat_history(chat_history)

        # 5. 构建 system prompt
        use_general = (intent == "general") or (not retrieval_confident)
        if intent == "business_query":
            system_prompt = self.SYSTEM_PROMPT_WITH_MEMORY_BUSINESS.format(
                long_term_memories=memory_text,
                chat_history=history_text,
            )
        elif use_general:
            system_prompt = self.SYSTEM_PROMPT_WITH_MEMORY_GENERAL.format(
                long_term_memories=memory_text,
                chat_history=history_text,
            )
        else:
            system_prompt = self.SYSTEM_PROMPT_WITH_MEMORY.format(
                long_term_memories=memory_text,
                chat_history=history_text,
            )

        # 6. 如果有知识库上下文，附加到 system prompt
        if kb_context and not use_general and intent != "business_query":
            system_prompt += f"\n\n## 参考资料\n{kb_context}"

        # 6.5 比较类查询：追加比较指令
        if query_type == "comparison" and not use_general and intent != "business_query":
            system_prompt += (
                "\n\n## 比较回答要求\n"
                "这是一个比较类问题。请先分别列出每个被比较实体的相关信息，然后进行对比分析，"
                "最后给出明确的结论。如果缺少某个实体的信息，请明确说明。"
            )

        # 7. 构建 messages 数组
        messages = [{"role": "system", "content": system_prompt}]

        # 加入最近的对话历史作为 messages（最近 3 轮，控制 prompt token 量）
        recent_history = chat_history[-6:] if len(chat_history) > 6 else chat_history
        for msg in recent_history:
            messages.append({
                "role": msg["role"],
                "content": msg["content"],
            })

        # 加入当前用户问题
        messages.append({"role": "user", "content": user_query})

        return messages, [asdict(m) for m in relevant_memories]

    def _format_memories(self, memories: List[Memory]) -> str:
        """格式化长期记忆为文本"""
        if not memories:
            return "（无相关记忆）"

        lines = []
        for i, m in enumerate(memories, 1):
            type_label = {
                "fact": "事实",
                "preference": "偏好",
                "decision": "决策",
                "identity": "身份",
                "summary": "摘要",
            }.get(m.memory_type, "信息")
            lines.append(f"{i}. [{type_label}] {m.content}")
        return "\n".join(lines)

    def _format_chat_history(self, messages: List[Dict]) -> str:
        """格式化对话历史为文本"""
        if not messages:
            return "（无对话历史）"

        lines = []
        for msg in messages[-12:]:  # 最近 6 轮
            role = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:200]  # 截断过长的消息
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    # ============================================================
    # 记忆生命周期管理
    # ============================================================

    async def cleanup_expired(self):
        """清理过期记忆"""
        now = time.time()
        expire_seconds = self.expire_days * 86400
        expired_ids = []

        for mid, m in self._memories.items():
            if m.expired:
                expired_ids.append(mid)
                continue
            # 30天未访问 + 重要性低于阈值 → 过期
            if (now - m.last_accessed > expire_seconds and m.importance < self.min_importance):
                m.expired = True
                expired_ids.append(mid)
                logger.info(f"记忆过期: [{m.memory_type}] {m.content[:50]} (id={mid})")

        # 从 Milvus 删除过期向量
        for mid in expired_ids:
            m = self._memories.get(mid)
            if m and m.vector_id:
                try:
                    await self._delete_memory_vector(m.vector_id)
                except Exception as e:
                    logger.warning(f"删除记忆向量失败: {e}")

        if expired_ids:
            self._persist_memories()
            logger.info(f"清理了 {len(expired_ids)} 条过期记忆")

    async def delete_memory(self, memory_id: str) -> bool:
        """删除指定记忆"""
        m = self._memories.pop(memory_id, None)
        if not m:
            return False

        if m.vector_id:
            try:
                await self._delete_memory_vector(m.vector_id)
            except Exception:
                pass

        self._persist_memories()
        logger.info(f"删除记忆: {memory_id}")
        return True

    async def update_memory(self, memory_id: str, content: str = None,
                            importance: float = None) -> bool:
        """更新记忆内容或重要性"""
        m = self._memories.get(memory_id)
        if not m:
            return False

        if content is not None:
            m.content = content
            m.fingerprint = hashlib.md5(content.encode()).hexdigest()[:16]
        if importance is not None:
            m.importance = max(0.0, min(1.0, importance))

        m.last_accessed = time.time()
        self._persist_memories()
        return True

    async def _delete_memory_vector(self, vector_id: str):
        """从 Milvus 删除记忆向量"""
        if not self.milvus:
            return

        def _do():
            from pymilvus import Collection, connections, utility
            connections.connect(alias="default", host=self.milvus.host, port=self.milvus.port)
            if not utility.has_collection(self.collection_name):
                return
            col = Collection(self.collection_name)
            from app.vectorstore.hybrid_store import _build_eq_expr
            # _milvus_escape 已返回带引号字面量，必须用 _build_eq_expr 统一拼接，
            # 再外包引号会生成 memory_id == ""x"" 非法表达式导致删除静默失败
            col.delete(_build_eq_expr("memory_id", vector_id))
            col.flush()
            if self._memory_collection is not None:
                self._memory_collection = None

        await asyncio.to_thread(_do)

    # ============================================================
    # 持久化
    # ============================================================

    def _persist_memories(self):
        """将记忆写入 JSONL 文件"""
        try:
            file_path = os.path.join(self.storage_path, "memories.jsonl")
            with open(file_path, "w", encoding="utf-8") as f:
                for m in self._memories.values():
                    f.write(json.dumps(asdict(m), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"记忆持久化失败: {e}")

    def _load_memories(self):
        """从 JSONL 文件加载记忆"""
        file_path = os.path.join(self.storage_path, "memories.jsonl")
        if not os.path.exists(file_path):
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    m = Memory(**data)
                    self._memories[m.memory_id] = m
            logger.info(f"加载了 {len(self._memories)} 条记忆")
        except Exception as e:
            logger.error(f"记忆加载失败: {e}")

    def get_memory_stats(self) -> Dict:
        """获取记忆统计信息"""
        total = len(self._memories)
        active = sum(1 for m in self._memories.values() if not m.expired)
        by_type = {}
        for m in self._memories.values():
            if not m.expired:
                by_type[m.memory_type] = by_type.get(m.memory_type, 0) + 1

        return {
            "total": total,
            "active": active,
            "expired": total - active,
            "by_type": by_type,
        }
