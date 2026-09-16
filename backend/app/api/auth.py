"""
认证 API - 登录 / 首个用户注册 / 当前用户信息

用户存储在 PostgreSQL users 表。首次部署时系统无用户，
注册接口开放用于创建管理员账户；之后注册关闭（单用户模式）。
"""

import re
import logging
import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..core.database import get_pool
from ..core.security import (
    create_access_token,
    hash_password,
    verify_password,
    verify_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class CurrentUser(BaseModel):
    """请求级用户上下文，由 get_current_user 依赖注入"""
    username: str
    role_id: str = "admin"
    role_name: str = "管理员"
    scope_ids: list = ["all"]


async def get_current_user(username: str = Depends(verify_token)) -> CurrentUser:
    """从 JWT 解析出的 username 查库得到完整用户上下文（角色 + 可见 scope）。
    每次请求都从 DB 取最新值，admin 改角色后立即生效。"""
    from ..services.permission_service import get_user_role
    role = await get_user_role(username)
    if role is None:
        return CurrentUser(username=username)
    return CurrentUser(
        username=username,
        role_id=role["role_id"],
        role_name=role["role_name"],
        scope_ids=role["scope_ids"],
    )

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]{3,64}$")


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class RegisterRequest(LoginRequest):
    pass


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    expires_in_minutes: int


async def _fetch_user(username: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT username, password_hash FROM users WHERE username = $1", username
        )


async def _count_users() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*) FROM users")


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    from ..core.config import settings
    try:
        user = await _fetch_user(request.username)
    except Exception as e:
        logger.error(f"认证数据库不可用: {e}")
        raise HTTPException(status_code=503, detail="认证服务暂不可用，请稍后重试")
    if user is None or not verify_password(request.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = create_access_token(request.username)
    return TokenResponse(
        access_token=token,
        username=request.username,
        expires_in_minutes=settings.JWT_EXPIRE_MINUTES,
    )


@router.post("/register", response_model=TokenResponse)
async def register(request: RegisterRequest):
    """仅当系统中还没有任何用户时开放（创建首个管理员账户）"""
    if not _USERNAME_RE.match(request.username):
        raise HTTPException(status_code=400, detail="用户名只能包含字母/数字/下划线/连字符，3-64 位")
    try:
        if await _count_users() > 0:
            raise HTTPException(status_code=403, detail="系统已有用户，注册已关闭")
        # 确保预置角色已存在
        from ..services.permission_service import ensure_builtin_roles
        await ensure_builtin_roles()

        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO users (username, password_hash) VALUES ($1, $2)",
                    request.username, hash_password(request.password),
                )
                # 首个用户默认 admin
                await conn.execute(
                    "INSERT INTO user_roles (username, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    request.username, "admin",
                )
            except asyncpg.UniqueViolationError:
                raise HTTPException(status_code=409, detail="用户名已存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"注册失败: {e}")
        raise HTTPException(status_code=503, detail="认证服务暂不可用，请稍后重试")

    from ..core.config import settings
    return TokenResponse(
        access_token=create_access_token(request.username),
        username=request.username,
        expires_in_minutes=settings.JWT_EXPIRE_MINUTES,
    )


class MeResponse(BaseModel):
    username: str
    role_id: str = "admin"
    role_name: str = "管理员"
    scope_ids: list = ["all"]


@router.get("/me", response_model=MeResponse)
async def me(user: CurrentUser = Depends(get_current_user)):
    return MeResponse(
        username=user.username,
        role_id=user.role_id,
        role_name=user.role_name,
        scope_ids=user.scope_ids,
    )
