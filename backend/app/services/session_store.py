"""
会话存储 - 基于 Redis 的短期对话记忆
"""

import json
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionStore:
    """基于 Redis 的会话短期记忆"""

    def __init__(self, redis_url: str, max_messages: int = 60, ttl: int = 86400, password: str = ""):
        self.redis_url = redis_url
        self.max_messages = max_messages
        self.ttl = ttl
        self.password = password
        self.redis = None
        self._connected = False
        # 会话归属与单会话 TTL（PII 隔离）：Redis 不可用时用进程内字典兜底
        self._session_meta_mem: Dict[str, Dict] = {}
        self._session_ttls: Dict[str, int] = {}

    async def connect(self):
        """连接 Redis"""
        if self._connected:
            return
        try:
            import redis.asyncio as aioredis
            from urllib.parse import urlparse
            parsed = urlparse(self.redis_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 6379
            db = int(parsed.path.lstrip("/")) if parsed.path and parsed.path != "/" else 0
            password = parsed.password or self.password or None
            self.redis = aioredis.Redis(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=True,
            )
            await self.redis.ping()
            self._connected = True
            logger.info(f"Redis连接成功: {host}:{port}/{db}")
        except Exception as e:
            logger.warning(f"Redis连接失败: {e}，将使用内存存储")
            self.redis = None
            self._connected = True  # 标记已尝试连接，避免重复尝试

    def _session_key(self, session_id: str) -> str:
        return f"session:{session_id}:messages"

    def _session_meta_key(self, session_id: str) -> str:
        return f"session:{session_id}:meta"

    async def bind_session(self, session_id: str, owner: str, ttl: int = 0):
        """绑定会话归属，并可单独缩短该会话的原文留存期。

        入园办理会话默认 2 小时（由 chat API 传入），普通问答维持全局 TTL。
        """
        await self.connect()
        effective = int(ttl or self.ttl)
        self._session_ttls[session_id] = effective
        self._session_meta_mem[session_id] = {"owner": owner, "ttl": effective}
        if self.redis:
            try:
                await self.redis.hset(self._session_meta_key(session_id), mapping={
                    "owner": owner,
                    "ttl": str(effective),
                    "session_id": session_id,
                    "last_active": str(time.time()),
                })
                await self.redis.expire(self._session_meta_key(session_id), effective)
            except Exception as e:
                logger.error(f"Redis绑定会话归属失败: {e}")

    async def get_owner(self, session_id: str) -> str:
        """返回会话归属用户名；历史会话无归属时返回空串（调用方按拒绝处理）。"""
        await self.connect()
        if self.redis:
            try:
                return await self.redis.hget(self._session_meta_key(session_id), "owner") or ""
            except Exception as e:
                logger.error(f"Redis读取会话归属失败: {e}")
        return self._session_meta_mem.get(session_id, {}).get("owner", "")

    async def _effective_ttl(self, session_id: str) -> int:
        if self.redis:
            try:
                v = await self.redis.hget(self._session_meta_key(session_id), "ttl")
                if v:
                    return int(v)
            except Exception:
                pass
        return self._session_ttls.get(session_id, self.ttl)

    async def add_message(self, session_id: str, role: str, content: str,
                          citations: list = None, metadata: dict = None):
        """追加一条消息到会话"""
        await self.connect()
        message = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        if citations:
            message["citations"] = citations
        if metadata:
            message["metadata"] = metadata

        message_json = json.dumps(message, ensure_ascii=False)
        key = self._session_key(session_id)
        ttl = await self._effective_ttl(session_id)
        owner = self._session_meta_mem.get(session_id, {}).get("owner", "")

        if self.redis:
            try:
                pipe = self.redis.pipeline()
                pipe.rpush(key, message_json)
                pipe.ltrim(key, -self.max_messages, -1)
                pipe.expire(key, ttl)
                # 更新会话元数据（hset 不覆盖已有的 owner 字段）
                meta_key = self._session_meta_key(session_id)
                meta = {"last_active": str(time.time()), "session_id": session_id}
                if owner:
                    meta["owner"] = owner
                pipe.hset(meta_key, mapping=meta)
                pipe.expire(meta_key, ttl)
                await pipe.execute()
            except Exception as e:
                logger.error(f"Redis写入失败: {e}")
        else:
            # 内存回退（无 Redis 时）
            if not hasattr(self, '_memory_store'):
                self._memory_store = {}
            if session_id not in self._memory_store:
                self._memory_store[session_id] = []
            self._memory_store[session_id].append(message)
            self._memory_store[session_id] = self._memory_store[session_id][-self.max_messages:]

    async def get_messages(self, session_id: str, limit: int = 60) -> List[Dict]:
        """获取会话的最近 N 条消息"""
        await self.connect()
        key = self._session_key(session_id)

        if self.redis:
            try:
                raw_messages = await self.redis.lrange(key, -limit, -1)
                return [json.loads(m) for m in raw_messages]
            except Exception as e:
                logger.error(f"Redis读取失败: {e}")
                return []
        else:
            if not hasattr(self, '_memory_store'):
                self._memory_store = {}
            return self._memory_store.get(session_id, [])[-limit:]

    async def clear_session(self, session_id: str):
        """清空会话"""
        await self.connect()
        key = self._session_key(session_id)
        meta_key = self._session_meta_key(session_id)

        if self.redis:
            try:
                await self.redis.delete(key, meta_key)
            except Exception as e:
                logger.error(f"Redis删除失败: {e}")
        else:
            if hasattr(self, '_memory_store') and session_id in self._memory_store:
                del self._memory_store[session_id]
        self._session_meta_mem.pop(session_id, None)
        self._session_ttls.pop(session_id, None)

    async def list_sessions(self, limit: int = 50, owner: str = None) -> List[Dict]:
        """列出最近的会话；传入 owner 时只返回该用户的会话。"""
        await self.connect()

        if self.redis:
            try:
                # 扫描所有 session meta keys
                sessions = []
                async for key in self.redis.scan_iter("session:*:meta", count=100):
                    meta = await self.redis.hgetall(key)
                    if meta:
                        session_id = meta.get("session_id", "")
                        session_owner = meta.get("owner", "")
                        if owner and session_owner != owner:
                            continue
                        # 获取最后一条消息作为预览
                        messages_key = self._session_key(session_id)
                        last_msg = await self.redis.lindex(messages_key, -1)
                        preview = ""
                        if last_msg:
                            msg = json.loads(last_msg)
                            preview = msg.get("content", "")[:80]

                        sessions.append({
                            "session_id": session_id,
                            "owner": session_owner,
                            "last_active": float(meta.get("last_active", 0)),
                            "preview": preview,
                        })
                sessions.sort(key=lambda s: s["last_active"], reverse=True)
                return sessions[:limit]
            except Exception as e:
                logger.error(f"Redis列出会话失败: {e}")
                return []
        else:
            if not hasattr(self, '_memory_store'):
                return []
            sessions = []
            for sid, msgs in self._memory_store.items():
                session_owner = self._session_meta_mem.get(sid, {}).get("owner", "")
                if owner and session_owner != owner:
                    continue
                preview = msgs[-1]["content"][:80] if msgs else ""
                sessions.append({
                    "session_id": sid,
                    "owner": session_owner,
                    "last_active": msgs[-1]["timestamp"] if msgs else 0,
                    "preview": preview,
                })
            sessions.sort(key=lambda s: s["last_active"], reverse=True)
            return sessions[:limit]

    async def get_session_count(self) -> int:
        """获取会话总数"""
        await self.connect()
        if self.redis:
            try:
                count = 0
                async for _ in self.redis.scan_iter("session:*:meta", count=100):
                    count += 1
                return count
            except Exception:
                return 0
        else:
            return len(getattr(self, '_memory_store', {}))
