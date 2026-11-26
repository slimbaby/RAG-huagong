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
    # 司机入园专用：只放园区入园制度 / 材料清单 / 常见问题，
    # 司机能问「入园要带什么」，但看不到财务、生产等其它部门文档。
    {"scope_id": "entry_public", "name": "入园公共须知"},
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
    # 司机入园专用角色：仅可见「入园公共须知」scope。
    # 若给空 scope，retrieval_node 会直接短路，司机问「入园要带什么材料」
    # 在 RAG 侧拿不到任何内容，与「只读问题留在知识库问答」的决策冲突。
    {"role_id": "driver", "role_name": "司机", "scope_ids": ["entry_public"]},
    # 配置后台角色：只用于管理流程配置，禁止出现在 applicant/reviewer 列表（§7.4）
    {"role_id": "config_editor", "role_name": "流程配置编辑", "scope_ids": ["general"]},
    {"role_id": "config_publisher", "role_name": "流程配置发布", "scope_ids": ["general"]},
    # 后续流程示例角色
    {"role_id": "finance_user", "role_name": "财务经办", "scope_ids": ["finance"]},
    {"role_id": "warehouse_user", "role_name": "仓库经办", "scope_ids": ["production_scheduling"]},
    {"role_id": "qc_user", "role_name": "质检经办", "scope_ids": ["quality"]},
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
        # 升级迁移：ON CONFLICT DO NOTHING 不会更新已存在的角色记录。
        # 旧版本 driver 的 scope_ids 是 []，不迁移会导致「入园公共须知」
        # 永远不生效。这里只修空值，不覆盖管理员后来手工调整过的权限。
        import json
        await conn.execute(
            """
            UPDATE roles SET scope_ids = $1::jsonb
            WHERE role_id = 'driver'
              AND (scope_ids IS NULL OR scope_ids::text IN ('[]', 'null', ''))
            """,
            json.dumps(["entry_public"]),
        )


SERVICE_ROLE = {"role_id": "service", "role_name": "服务账号", "scope_ids": ["all"]}


async def get_user_role(username: str) -> Optional[dict]:
    """返回用户的主角色 {role_id, role_name, scope_ids}。

    三种情形：
    - 服务账号（settings.SERVICE_ACCOUNTS 显式白名单，如评测 runner 的
      eval_runner）：不入库，直接授予 service 角色；
    - users 表中存在但未分配角色：返回 unassigned 最小权限（无任何 scope）。
      此前向后兼容回落 admin，在单用户部署下无害，但入园预约引入批量司机
      账号后，任何漏配角色的账号都会直接拿到全量权限，故改为最小权限；
    - users 表中不存在的用户名：返回 None，调用方应拒绝访问。
      此前该情形回落 admin——任何持有效签名 token 的幽灵用户名
      都能获得全量权限，属越权通道。
    """
    from ..core.config import settings
    if username in (settings.SERVICE_ACCOUNTS or []):
        return dict(SERVICE_ROLE)
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
        # 无角色记录：仅确认 users 表中真实存在该用户，且只给最小权限。
        # 绝不回落 admin —— 司机账号漏配角色时不能变成管理员。
        exists = await conn.fetchval(
            "SELECT 1 FROM users WHERE username = $1", username
        )
    if exists:
        logger.warning(f"用户 {username} 存在但未分配角色，按最小权限处理")
        return {"role_id": "unassigned", "role_name": "未分配角色", "scope_ids": []}
    return None


async def get_visible_scopes(username: str) -> Optional[List[str]]:
    """返回用户可见的 scope 列表。None 表示 admin 看全部，无需过滤。

    注意语义：get_user_role 返回 None（users 表查无此人）时返回空列表
    （无任何可见 scope），而不是 None——None 是 admin 特权语义，
    不能授予身份未知的请求者。
    """
    role = await get_user_role(username)
    if role is None:
        return []
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
