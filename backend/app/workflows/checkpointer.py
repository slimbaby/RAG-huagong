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
from typing import Optional

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
