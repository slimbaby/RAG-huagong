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

        # 阶段 1 迁移后，entry_* 老表已重命名为 *_legacy，同名关系变成只读兼容视图；
        # 此时跳过所有建表 / 补列 / 索引语句，避免对视图执行 DDL 报错（D15）。
        _entry_is_view = bool(await conn.fetchval(
            "SELECT 1 FROM pg_views WHERE schemaname='public' AND viewname='entry_applications'"
        ))

        # ── 司机入园预约 ──
        # 手动申请与 AI 辅助申请共用同一张业务表：source 始终是 manual，
        # assisted_by_ai 标记「是否由对话 Agent 预填」，用于统计 AI 使用率。
        # status 承载 draft → pending → approved/rejected/cancelled 状态机，
        # rejected 可经 reopen 回到 draft 重提（保留同一单号与审计链）。
        # form_data 存完整表单（含附件 ID 映射），冗余列只用于列表检索与排序。
        # workflow_run_id / session_id / idempotency_key 三列是旧 workflow
        # 原型遗留，方案乙不使用、不再写入；保留列是为兼容已有库数据，
        # 下个版本随数据清理一并 DROP。
        if not _entry_is_view: await conn.execute("""
            CREATE TABLE IF NOT EXISTS entry_applications (
                id VARCHAR(32) PRIMARY KEY,
                applicant_username VARCHAR(64) NOT NULL,
                source VARCHAR(16) NOT NULL DEFAULT 'manual',
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                form_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                driver_name VARCHAR(64) DEFAULT '',
                phone VARCHAR(32) DEFAULT '',
                plate_no VARCHAR(32) DEFAULT '',
                company_name VARCHAR(128) DEFAULT '',
                entry_time TIMESTAMP NULL,
                exit_time TIMESTAMP NULL,
                review_comment TEXT DEFAULT '',
                reviewed_by VARCHAR(64) DEFAULT '',
                reviewed_at TIMESTAMP NULL,
                confirmed_at TIMESTAMP NULL,
                assisted_by_ai BOOLEAN NOT NULL DEFAULT FALSE,
                pass_no VARCHAR(40) DEFAULT '',
                qr_payload TEXT DEFAULT '',
                pass_issued_at TIMESTAMP NULL,
                workflow_run_id VARCHAR(64) DEFAULT '',
                session_id VARCHAR(64) DEFAULT '',
                idempotency_key VARCHAR(160) UNIQUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """)
        # 证件/车辆照片独立存储：所有权归上传人，绝不写入知识库索引。
        # application_id 在提交时绑定：一张证件只能被一张申请单使用，
        # 从根上避免「多次申请复用上一次证件」的串图问题。
        if not _entry_is_view: await conn.execute("""
            CREATE TABLE IF NOT EXISTS entry_attachments (
                id VARCHAR(40) PRIMARY KEY,
                owner_username VARCHAR(64) NOT NULL,
                application_id VARCHAR(32) NOT NULL DEFAULT '',
                kind VARCHAR(32) NOT NULL,
                filename VARCHAR(255) NOT NULL,
                storage_path VARCHAR(512) NOT NULL,
                content_type VARCHAR(128) DEFAULT '',
                size_bytes BIGINT DEFAULT 0,
                ocr_text JSONB,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        if not _entry_is_view: await conn.execute("""
            CREATE TABLE IF NOT EXISTS entry_audit_log (
                id SERIAL PRIMARY KEY,
                application_id VARCHAR(32) NOT NULL,
                action VARCHAR(32) NOT NULL,
                actor_username VARCHAR(64) NOT NULL,
                from_status VARCHAR(16) DEFAULT '',
                to_status VARCHAR(16) DEFAULT '',
                detail JSONB DEFAULT '{}'::jsonb,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)

        # 索引
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_layer ON eval_cases(layer)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_doc ON eval_cases(source_doc_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_cases_stale ON eval_cases(stale)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_reports_time ON eval_reports(created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_doc_scopes_scope ON doc_scopes(scope_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_user_roles_role ON user_roles(role_id)")
        if not _entry_is_view: await conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_app_owner ON entry_applications(applicant_username, created_at DESC)")
        if not _entry_is_view: await conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_app_status ON entry_applications(status, created_at DESC)")
        if not _entry_is_view: await conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_app_run ON entry_applications(workflow_run_id)")
        if not _entry_is_view: await conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_att_owner ON entry_attachments(owner_username)")
        if not _entry_is_view: await conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_att_app ON entry_attachments(application_id)")
        if not _entry_is_view: await conn.execute("CREATE INDEX IF NOT EXISTS idx_entry_audit_app ON entry_audit_log(application_id, created_at)")

        # 幂等列迁移：CREATE TABLE IF NOT EXISTS 不会给已存在的旧表补列，
        # 从 P0 原型升级到方案乙时这里必须显式补，否则审核/通行证接口全线报错。
        if not _entry_is_view: await conn.execute("""
            ALTER TABLE entry_applications
                ADD COLUMN IF NOT EXISTS assisted_by_ai BOOLEAN NOT NULL DEFAULT FALSE,
                ADD COLUMN IF NOT EXISTS pass_no VARCHAR(40) DEFAULT '',
                ADD COLUMN IF NOT EXISTS qr_payload TEXT DEFAULT '',
                ADD COLUMN IF NOT EXISTS pass_issued_at TIMESTAMP NULL
        """)
        if not _entry_is_view: await conn.execute("""
            ALTER TABLE entry_attachments
                ADD COLUMN IF NOT EXISTS application_id VARCHAR(32) NOT NULL DEFAULT ''
        """)

        # ── 多流程配置化平台（通用 5 张表；新增流程只写配置数据，零 DDL）──
        # 设计文档 docs/10-多流程配置化平台设计.md §4 / D1(1A)。
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_definitions (
                flow_id VARCHAR(64) PRIMARY KEY,
                published_version INT NOT NULL DEFAULT 0,
                draft JSONB NOT NULL DEFAULT '{}'::jsonb,
                draft_revision INT NOT NULL DEFAULT 0,
                updated_by VARCHAR(64) NOT NULL DEFAULT '',
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                rollout JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_definition_versions (
                flow_id VARCHAR(64) NOT NULL,
                version INT NOT NULL,
                definition JSONB NOT NULL,
                diff JSONB NOT NULL DEFAULT '{}'::jsonb,
                published_by VARCHAR(64) NOT NULL DEFAULT '',
                submitted_by VARCHAR(64) NOT NULL DEFAULT '',
                published_at TIMESTAMP NOT NULL DEFAULT NOW(),
                note TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (flow_id, version)
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_applications (
                id VARCHAR(40) PRIMARY KEY,
                flow_id VARCHAR(64) NOT NULL,
                definition_version INT NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'draft',
                applicant_username VARCHAR(64) NOT NULL,
                source VARCHAR(16) NOT NULL DEFAULT 'manual',
                assisted_by_ai BOOLEAN NOT NULL DEFAULT FALSE,
                form_data JSONB NOT NULL DEFAULT '{}'::jsonb,
                attachments JSONB NOT NULL DEFAULT '{}'::jsonb,
                result JSONB NOT NULL DEFAULT '{}'::jsonb,
                title VARCHAR(128) NOT NULL DEFAULT '',
                review_comment TEXT NOT NULL DEFAULT '',
                reviewed_by VARCHAR(64) NOT NULL DEFAULT '',
                reviewed_at TIMESTAMP NULL,
                confirmed_at TIMESTAMP NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_attachments (
                id VARCHAR(40) PRIMARY KEY,
                flow_id VARCHAR(64) NOT NULL,
                owner_username VARCHAR(64) NOT NULL,
                application_id VARCHAR(40) NOT NULL DEFAULT '',
                kind VARCHAR(32) NOT NULL,
                filename VARCHAR(255) NOT NULL DEFAULT '',
                storage_path VARCHAR(512) NOT NULL DEFAULT '',
                content_type VARCHAR(128) NOT NULL DEFAULT '',
                size_bytes BIGINT NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS flow_audit_log (
                id BIGSERIAL PRIMARY KEY,
                flow_id VARCHAR(64) NOT NULL DEFAULT '',
                application_id VARCHAR(40) NOT NULL DEFAULT '',
                action VARCHAR(40) NOT NULL,
                actor VARCHAR(64) NOT NULL DEFAULT '',
                from_status VARCHAR(16) NOT NULL DEFAULT '',
                to_status VARCHAR(16) NOT NULL DEFAULT '',
                detail JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMP NOT NULL DEFAULT NOW()
            )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_app_flow_status ON flow_applications(flow_id, status, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_app_owner ON flow_applications(applicant_username, created_at DESC)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_att_owner ON flow_attachments(owner_username, flow_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_att_app ON flow_attachments(application_id)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_audit_app ON flow_audit_log(application_id, created_at)")
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_flow_audit_flow ON flow_audit_log(flow_id, created_at DESC)")

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
