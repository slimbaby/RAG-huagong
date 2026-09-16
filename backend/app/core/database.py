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
        # 用户表（JWT认证）
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username VARCHAR(64) UNIQUE NOT NULL,
                password_hash VARCHAR(255) NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # ── RBAC 权限表 ──
        # 角色表：role_id 是扁平标识（如 'finance'/'hr'/'production'/'admin'），
        # scope_ids 是该角色可见的 scope 列表；含 "all" 表示可见全部
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS roles (
                role_id VARCHAR(32) PRIMARY KEY,
                role_name VARCHAR(64) NOT NULL,
                scope_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # 用户-角色关联（多对一；预留扩展多角色）
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_roles (
                username VARCHAR(64) NOT NULL,
                role_id VARCHAR(32) NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
                PRIMARY KEY (username, role_id)
            )
        """)
        # 文档-scope 多对多关联（一个文档可属于多个 scope）
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS doc_scopes (
                doc_id VARCHAR(64) NOT NULL,
                scope_id VARCHAR(64) NOT NULL,
                PRIMARY KEY (doc_id, scope_id)
            )
        """)

        # 索引
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_layer ON eval_cases(layer)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_doc ON eval_cases(source_doc_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_stale ON eval_cases(stale)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_reports_time ON eval_reports(created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_scopes_scope ON doc_scopes(scope_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id)")

    # 幂等：老库 users 表没有 role_id 列时补一个（向后兼容，已废弃但仍保留）
    try:
        async with pool.acquire() as conn:
            cols = await conn.fetch("SELECT column_name FROM information_schema.columns WHERE table_name='users'")
            if not any(c["column_name"] == "role_id" for c in cols):
                await conn.execute("ALTER TABLE users ADD COLUMN role_id VARCHAR(32) DEFAULT 'admin'")
                logger.info("users 表补齐 role_id 列（默认 admin）")
    except Exception as e:
        logger.warning(f"users 表补齐 role_id 失败: {e}")

    # 预置 admin 角色（scope_ids=["all"] 表示可见全部）
    try:
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO roles (role_id, role_name, scope_ids)
                VALUES ('admin', '管理员', '["all"]'::jsonb)
                ON CONFLICT (role_id) DO NOTHING
            """)
    except Exception as e:
        logger.warning(f"预置 admin 角色失败: {e}")

    logger.info("评测数据表初始化完成")
