"""
RBAC 权限服务

模型：
  用户 ──属于──▶ 角色 (role_id) ──映射──▶ 可见 scope 列表 (scope_ids)
  文档 ──多对多──▶ scope (doc_scopes 表)

admin 角色的 scope_ids 含 "all"，解析时返回 None 表示不加过滤（看全部）。
"""

import logging
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# 系统预置 scope 定义（前端上传下拉框的选项来源）
# 初期扁平结构，需要层级时再在 PG 加 parent_scope_id
BUILTIN_SCOPES = [
    {"scope_id": "finance", "name": "财务"},
    {"scope_id": "hr", "name": "人力资源"},
    {"scope_id": "production_scheduling", "name": "排单生产"},
    {"scope_id": "safety", "name": "安全环保"},
    {"scope_id": "quality", "name": "质量"},
    {"scope_id": "general", "name": "综合"},
]

# 系统预置角色（首次注册 / 初始化时按需插入 PG）
BUILTIN_ROLES = [
    {"role_id": "admin", "role_name": "管理员", "scope_ids": ["all"]},
    {"role_id": "finance", "role_name": "财务", "scope_ids": ["finance"]},
    {"role_id": "hr", "role_name": "人力资源", "scope_ids": ["hr"]},
    {"role_id": "production", "role_name": "生产车间", "scope_ids": ["production_scheduling"]},
    {"role_id": "safety", "role_name": "安全环保", "scope_ids": ["safety"]},
    {"role_id": "quality", "role_name": "质量", "scope_ids": ["quality"]},
    {"role_id": "general_viewer", "role_name": "综合查看", "scope_ids": ["general"]},
]


async def ensure_builtin_roles():
    """幂等地把预置角色写入 PG（首次启动 / 升级时调用）"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        for r in BUILTIN_ROLES:
            import json
            await conn.execute(
                """
                INSERT INTO roles (role_id, role_name, scope_ids)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (role_id) DO NOTHING
                """,
                r["role_id"], r["role_name"], json.dumps(r["scope_ids"]),
            )


async def get_user_role(username: str) -> Optional[dict]:
    """返回用户的主角色 {role_id, role_name, scope_ids}，未分配返回 None"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT r.role_id, r.role_name, r.scope_ids
            FROM user_roles ur
            JOIN roles r ON r.role_id = ur.role_id
            WHERE ur.username = $1
            LIMIT 1
            """,
            username,
        )
        if row:
            # asyncpg 返回的 JSONB 可能是字符串，需要手动解析
            scope_ids_raw = row["scope_ids"]
            if isinstance(scope_ids_raw, str):
                import json
                try:
                    scope_ids = json.loads(scope_ids_raw)
                except:
                    scope_ids = []
            else:
                scope_ids = list(scope_ids_raw) if scope_ids_raw else []
            return {
                "role_id": row["role_id"],
                "role_name": row["role_name"],
                "scope_ids": scope_ids,
            }
    # 向后兼容：老用户没有 user_roles 记录时，按 admin 处理
    return {"role_id": "admin", "role_name": "管理员", "scope_ids": ["all"]}


async def get_visible_scopes(username: str) -> Optional[List[str]]:
    """返回用户可见的 scope 列表。None 表示 admin 看全部，无需过滤。"""
    role = await get_user_role(username)
    if role is None:
        return None
    scope_ids = role["scope_ids"]
    if "all" in scope_ids:
        return None
    return scope_ids


async def get_doc_scopes(doc_id: str) -> List[str]:
    """返回某文档绑定的所有 scope_id"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT scope_id FROM doc_scopes WHERE doc_id = $1", doc_id
        )
        return [r["scope_id"] for r in rows]


async def set_doc_scopes(doc_id: str, scope_ids: List[str]):
    """原子替换文档的 scope 绑定"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM doc_scopes WHERE doc_id = $1", doc_id)
            if scope_ids:
                values = [(doc_id, s) for s in scope_ids]
                await conn.executemany(
                    "INSERT INTO doc_scopes (doc_id, scope_id) VALUES ($1, $2)",
                    values,
                )


async def user_can_access_doc(username: str, doc_id: str) -> bool:
    """判断用户能否访问指定文档（admin 直接放行，否则看 scope 交集）"""
    visible = await get_visible_scopes(username)
    if visible is None:
        return True
    doc_scopes = await get_doc_scopes(doc_id)
    if not doc_scopes:
        # 历史文档未分配 scope 时，按可见处理（避免误拦截）
        return True
    return bool(set(visible) & set(doc_scopes))


async def get_accessible_doc_ids(username: str) -> Optional[Set[str]]:
    """返回用户可见的 doc_id 集合。None 表示看全部（admin）。"""
    visible = await get_visible_scopes(username)
    if visible is None:
        return None
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT doc_id FROM doc_scopes WHERE scope_id = ANY($1)",
            visible,
        )
        return {r["doc_id"] for r in rows}


def build_milvus_scope_filter(allowed_doc_ids: Optional[Set[str]]) -> str:
    """把可见 doc_id 集合转成 Milvus 表达式。None 表示不过滤（admin）。"""
    if allowed_doc_ids is None:
        return ""
    if not allowed_doc_ids:
        return 'doc_id == "__none__"'  # 空集，让检索返回 0 结果
    from ..vectorstore.hybrid_store import _milvus_escape
    escaped = [_milvus_escape(d) for d in allowed_doc_ids]
    return f'doc_id in [{", ".join(escaped)}]'


async def list_all_scopes() -> List[dict]:
    """列出系统可用的 scope（预置 + 已使用过的）"""
    from ..core.database import get_pool
    pool = await get_pool()
    builtin_ids = {s["scope_id"] for s in BUILTIN_SCOPES}
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT DISTINCT scope_id FROM doc_scopes")
    used_ids = {r["scope_id"] for r in rows} - builtin_ids
    result = list(BUILTIN_SCOPES)
    for sid in sorted(used_ids):
        result.append({"scope_id": sid, "name": sid})
    return result


async def list_all_roles() -> List[dict]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role_id, role_name, scope_ids FROM roles ORDER BY role_id"
        )
        result = []
        for r in rows:
            # asyncpg 返回的 JSONB 可能是字符串，需要手动解析
            scope_ids_raw = r["scope_ids"]
            if isinstance(scope_ids_raw, str):
                import json
                try:
                    scope_ids = json.loads(scope_ids_raw)
                except:
                    scope_ids = []
            else:
                scope_ids = list(scope_ids_raw) if scope_ids_raw else []
            result.append({
                "role_id": r["role_id"],
                "role_name": r["role_name"],
                "scope_ids": scope_ids,
            })
        return result


async def assign_user_role(username: str, role_id: str):
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM user_roles WHERE username = $1", username)
            await conn.execute(
                "INSERT INTO user_roles (username, role_id) VALUES ($1, $2)",
                username, role_id,
            )
