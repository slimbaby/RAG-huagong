"""
Scope / Role 管理 API

- GET  /scopes/             列出系统可用的 scope（上传下拉框的选项来源）
- GET  /roles/              列出所有角色
- POST /users/{username}/role   分配用户角色（admin 专属）
- GET  /users/              列出所有用户及其角色（admin 专属）
"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auth import CurrentUser, get_current_user
from ..services import permission_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_admin(user: CurrentUser):
    if user.role_id != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可执行此操作")


@router.get("/scopes/", summary="可用 scope 列表")
async def list_scopes(user: CurrentUser = Depends(get_current_user)):
    """返回系统所有可用 scope（预置 + 已使用过的）。所有登录用户可见。"""
    return await permission_service.list_all_scopes()


@router.get("/roles/", summary="角色列表")
async def list_roles(user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    return await permission_service.list_all_roles()


class AssignRoleRequest(BaseModel):
    role_id: str


@router.post("/users/{username}/role", summary="分配用户角色")
async def assign_role(
    username: str,
    req: AssignRoleRequest,
    user: CurrentUser = Depends(get_current_user),
):
    _require_admin(user)
    roles = await permission_service.list_all_roles()
    if not any(r["role_id"] == req.role_id for r in roles):
        raise HTTPException(status_code=400, detail=f"角色 {req.role_id} 不存在")
    await permission_service.assign_user_role(username, req.role_id)
    return {"username": username, "role_id": req.role_id}


class UserInfo(BaseModel):
    username: str
    role_id: str = "admin"
    role_name: str = "管理员"


@router.get("/users/", summary="用户列表")
async def list_users(user: CurrentUser = Depends(get_current_user)):
    _require_admin(user)
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.username, ur.role_id, r.role_name
            FROM users u
            LEFT JOIN user_roles ur ON ur.username = u.username
            LEFT JOIN roles r ON r.role_id = ur.role_id
            ORDER BY u.created_at
            """
        )
        return [
            {
                "username": r["username"],
                "role_id": r["role_id"] or "admin",
                "role_name": r["role_name"] or "管理员",
            }
            for r in rows
        ]
