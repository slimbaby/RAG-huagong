"""
PostgreSQL 连接池管理（评测系统专用）
使用 asyncpg 异步驱动
"""

import asyncpg
import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    """获取 PostgreSQL 连接池（懒初始化 + 自动建表）"""
    global _pool
    if _pool is not None:
        return _pool

    from .config import settings

    # 尝试连接目标数据库，不存在则自动创建
    try:
        _pool = await asyncpg.create_pool(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
            min_size=2,
            max_size=10,
        )
        await _init_tables(_pool)
        logger.info(f"PostgreSQL 连接池初始化完成 (db={settings.POSTGRES_DB})")
    except asyncpg.InvalidCatalogNameError:
        # 数据库不存在，连接默认库创建之
        logger.info(f"数据库 {settings.POSTGRES_DB} 不存在，正在创建...")
        conn = await asyncpg.connect(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database="postgres",
        )
        try:
            await conn.execute(f'CREATE DATABASE "{settings.POSTGRES_DB}"')
        finally:
            await conn.close()
        _pool = await asyncpg.create_pool(
            host=settings.POSTGRES_HOST,
            port=settings.POSTGRES_PORT,
            user=settings.POSTGRES_USER,
            password=settings.POSTGRES_PASSWORD,
            database=settings.POSTGRES_DB,
            min_size=2,
            max_size=10,
        )
        await _init_tables(_pool)
        logger.info(f"数据库 {settings.POSTGRES_DB} 创建完成，连接池初始化成功")

    return _pool


async def _init_tables(pool: asyncpg.Pool):
    """初始化评测相关表结构"""
    async with pool.acquire() as conn:
        # 评测用例表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_cases (
                id VARCHAR(64) PRIMARY KEY,
                data JSONB NOT NULL,
                layer VARCHAR(16) DEFAULT 'core',
                source_doc_id VARCHAR(64) DEFAULT '',
                stale BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # 评测报告表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS eval_reports (
                eval_id VARCHAR(64) PRIMARY KEY,
                data JSONB NOT NULL,
                avg_score FLOAT,
                gate_passed BOOLEAN,
                total_cases INT,
                passed_cases INT,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # 索引
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_layer ON eval_cases(layer)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_doc ON eval_cases(source_doc_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_stale ON eval_cases(stale)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_reports_time ON eval_reports(created_at DESC)")

    logger.info("评测数据表初始化完成")
