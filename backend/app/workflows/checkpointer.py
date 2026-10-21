"""
LangGraph Checkpointer 管理

用官方 AsyncSqliteSaver 替代原自研 Checkpointer：
- 只有框架自己知道「图执行到哪、下一步该干什么」，这套状态格式无法在外部复刻
- interrupt() 挂起 + Command(resume=...) 恢复是框架原生机制
- 断点续跑时前面的步骤不重跑（state 完整存在 checkpoint 里）

用 AsyncSqliteSaver 而非 SqliteSaver：后者是同步阻塞的 sqlite3，
在 FastAPI 的 async 事件循环里会卡住整个 worker。
"""

import asyncio
import logging
import os
from typing import Any, Optional

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "./checkpoints/workflows.db"

# 进程内单例：AsyncSqliteSaver 持有 aiosqlite 连接，每个连接会起一个后台线程，
# 不应随意创建多个。路径不变时复用同一实例；路径变化时复用旧连接
# （避免已编译的 graph 持有的 saver 被提前关闭而卡死）。
_saver: Optional[AsyncSqliteSaver] = None
_saver_path: Optional[str] = None
_saver_lock = asyncio.Lock()


async def get_checkpointer(db_path: Optional[str] = None) -> AsyncSqliteSaver:
    """获取 AsyncSqliteSaver 单例

    首次调用会创建目录、连接数据库、建表。
    """
    global _saver, _saver_path

    if db_path is None:
        try:
            from ..core.config import settings
            db_path = settings.WORKFLOW_CHECKPOINT_DB
        except Exception:
            db_path = None
    path = os.path.abspath(db_path or DEFAULT_DB_PATH)

    async with _saver_lock:
        if _saver is not None:
            return _saver

        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        import aiosqlite

        conn = await aiosqlite.connect(path)
        saver = AsyncSqliteSaver(conn)
        await saver.setup()
        _saver = saver
        _saver_path = path
        logger.info(f"LangGraph AsyncSqliteSaver 初始化完成: {path}")
        return saver


async def close_checkpointer() -> None:
    """关闭连接（应用关闭时调用）"""
    global _saver, _saver_path
    async with _saver_lock:
        if _saver is not None:
            try:
                await _saver.conn.close()
            except Exception as e:
                logger.warning(f"关闭 checkpointer 失败: {e}")
    _saver = None
    _saver_path = None


async def list_thread_checkpoints(
    saver: AsyncSqliteSaver,
    limit: int = 50,
) -> list:
    """列出最近的 checkpoint（每个线程只取最新一条）

    不能直接用 saver.alist(None)：它的 WHERE 条件是 `thread_id = ?` 精确匹配，
    不支持 `*` 通配（传 "*" 只会去查名为 "*" 的线程）。所以这里直接查表
    拿到 thread_id 列表，再逐个取最新快照。
    """
    try:
        async with saver.lock, saver.conn.execute(
            "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id"
        ) as cur:
            rows = await cur.fetchall()
    except Exception as e:
        logger.warning(f"查询 checkpoint 线程列表失败: {e}")
        return []

    results = []
    for thread_id, _ in rows:
        try:
            snapshot = await saver.aget_tuple(
                {"configurable": {"thread_id": thread_id}}
            )
        except Exception as e:
            logger.warning(f"读取线程 {thread_id} 快照失败: {e}")
            continue
        if snapshot is not None:
            results.append(snapshot)

    results.sort(key=lambda s: s.checkpoint.get("ts", ""), reverse=True)
    return results[:limit]


def extract_interrupt_payloads(source: Any) -> list:
    """从快照或 CheckpointTuple 里提取 interrupt 载荷

    两种来源的挂起信息位置不同：
    - StateSnapshot（graph.aget_state）：snapshot.tasks[].interrupts[].value
    - CheckpointTuple（saver.alist）：pending_writes 里 `__interrupt__` 通道的值
    """
    from langgraph._internal._constants import INTERRUPT

    out = []

    tasks = getattr(source, "tasks", None)
    if tasks is not None:
        for task in tasks:
            for it in getattr(task, "interrupts", None) or []:
                value = getattr(it, "value", None)
                if value is None and isinstance(it, dict):
                    value = it.get("value")
                if isinstance(value, dict):
                    out.append(value)
        return out

    # CheckpointTuple 形态：(task_id, channel, value) 三元组
    for write in getattr(source, "pending_writes", None) or []:
        try:
            _task_id, channel, value = write[0], write[1], write[2]
        except (TypeError, IndexError):
            continue
        if channel != INTERRUPT:
            continue
        for item in value if isinstance(value, (list, tuple)) else [value]:
            payload = getattr(item, "value", None)
            if isinstance(payload, dict):
                out.append(payload)

    return out
