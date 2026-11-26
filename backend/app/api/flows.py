"""
多流程配置化平台 API（docs/10 §8）

静态路由（/hooks /entry-points /render-config）一律声明在 /{flow_id} 之前，
避免被动态段吞掉。配置管理接口受 config_editor / config_publisher 角色保护，
运行时业务接口按配置的 permissions 段校验申请 / 审核角色。
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .auth import CurrentUser, get_current_user, require_admin
from ..flows import hooks as hook_registry
from ..flows import service as svc
from ..flows import store
from ..flows.model import FlowConfigError, FlowDefinition
from ..flows.registry import get_flow_registry
from ..flows.service import FlowError

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_ROOT = "./uploads/flows"
_MAX_FILE_SIZE = 10 * 1024 * 1024
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_MAGIC = {
    ".jpg": lambda b: b[:3] == b"\xff\xd8\xff",
    ".jpeg": lambda b: b[:3] == b"\xff\xd8\xff",
    ".png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    ".webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    ".pdf": lambda b: b[:5] == b"%PDF-",
}


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", value or "")[:64] or "unknown"


# ── 权限依赖 ──

def _config_roles() -> tuple:
    from ..core.config import settings
    extra = tuple(x for x in (getattr(settings, "FLOW_CONFIG_PUBLISHERS", []) or []) if x)
    return ("admin", "config_editor", "config_publisher") + extra


async def require_config_editor(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if user.role_id not in ("admin", "config_editor", "config_publisher"):
        raise HTTPException(status_code=403, detail="需要配置编辑权限（config_editor）")
    return user


async def require_config_publisher(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    from ..core.config import settings
    allowed = {"admin", "config_publisher"} | set(getattr(settings, "FLOW_CONFIG_PUBLISHERS", []) or [])
    if user.role_id not in allowed and user.username not in allowed:
        raise HTTPException(status_code=403, detail="需要配置发布权限（config_publisher）")
    return user


async def _definition(flow_id: str, user: CurrentUser,
                      *, application_version: Optional[int] = None,
                      session_version: Optional[int] = None) -> FlowDefinition:
    reg = get_flow_registry()
    await reg.load()
    d = await reg.resolve(flow_id, username=user.username, role_id=user.role_id,
                          application_version=application_version,
                          session_version=session_version)
    if d is None:
        raise HTTPException(status_code=404, detail=f"流程 {flow_id} 未发布或配置不可用")
    return d


def _check_applicant(d: FlowDefinition, user: CurrentUser) -> None:
    roles = d.applicant_roles
    if roles and user.role_id != "admin" and user.role_id not in roles:
        raise HTTPException(status_code=403, detail="当前角色不能申请该流程")


async def _visible(d: FlowDefinition, app_id: str, user: CurrentUser) -> Dict[str, Any]:
    app = await svc.get_application(app_id)
    if app is None or app["flow_id"] != d.flow_id:
        raise HTTPException(status_code=404, detail="申请单不存在")
    if app["applicant_username"] != user.username and user.role_id != "admin":
        raise HTTPException(status_code=403, detail="无权访问该申请单")
    return app


def _as_http(exc: Exception) -> HTTPException:
    if isinstance(exc, (FlowError, store.FlowStoreError)):
        return HTTPException(status_code=exc.status_code, detail=exc.message,
                             headers={"X-Flow-Extra": str(exc.extra) if isinstance(exc, store.FlowStoreError) else ""})
    if isinstance(exc, FlowConfigError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ── 请求体 ──

class DraftSave(BaseModel):
    definition: Dict[str, Any] = Field(default_factory=dict)
    expect_draft_revision: int = 0


class PublishRequest(BaseModel):
    expect_version: int = 0
    note: str = ""


class RollbackRequest(BaseModel):
    version: int
    note: str = ""


class TestCasesRequest(BaseModel):
    cases: list = Field(default_factory=list)
    expect_draft_revision: int = 0


class RolloutRequest(BaseModel):
    mode: str = "gray"  # gray / full
    version: int = 0
    users: list = Field(default_factory=list)
    groups: list = Field(default_factory=list)
    observe_days: int = 1
    note: str = ""


class ApplicationCreate(BaseModel):
    form_data: Dict[str, Any] = Field(default_factory=dict)
    assisted_by_ai: bool = False


class ApplicationUpdate(BaseModel):
    form_data: Dict[str, Any]


class FormValidateRequest(BaseModel):
    form_data: Dict[str, Any] = Field(default_factory=dict)
    for_submit: bool = True


class ParseRequest(BaseModel):
    text: str = ""
    slots: Dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    approved: bool
    comment: str = ""


class LifecycleRequest(BaseModel):
    state: str  # active / deprecated / retired


# ══════════════════ 静态路由（必须在 /{flow_id} 之前）══════════════════

@router.get("/hooks", summary="可用扩展点白名单（含 params_schema 与生命周期）")
async def list_hooks(user: CurrentUser = Depends(require_config_editor)):
    return {"hooks": hook_registry.list_hooks()}


@router.post("/hooks/{name}/lifecycle", summary="D16：扩展点三态下线（管理员）")
async def set_hook_lifecycle(name: str, request: LifecycleRequest,
                             admin: CurrentUser = Depends(require_admin)):
    try:
        if request.state == "retired":
            refs = await store.hook_references(name)
            hook_registry.assert_can_retire(name,
                                            published_references=refs["reference_count"],
                                            open_applications=refs["open_applications"])
        hook_registry.set_lifecycle(name, request.state)
        await store.audit("", "hook_lifecycle", admin.username,
                          {"name": name, "state": request.state})
        return {"name": name, "lifecycle": request.state}
    except (FlowConfigError, store.FlowStoreError) as e:
        raise _as_http(e)


@router.get("/hooks/{name}/references", summary="D16：扫描已发布版本与在途单据引用")
async def hook_references(name: str, admin: CurrentUser = Depends(require_admin)):
    return await store.hook_references(name)


@router.get("/entry-points", summary="前端动态菜单 / 快捷入口（仅渲染元数据，不含判定规则）")
async def entry_points(user: CurrentUser = Depends(get_current_user)):
    reg = get_flow_registry()
    await reg.load()
    items = []
    for d in reg.all_published():
        for ep in d.entry_points_for(user.role_id):
            items.append({**ep, "flow_id": d.flow_id, "flow_name": d.name,
                          "scope": d.scope, "version": d.version})
    items.sort(key=lambda x: int(x.get("order") or 100))
    return {"entry_points": items, "role_id": user.role_id}


@router.get("/template", summary="新建流程模板（后台用；返回可通过 dry-run 的最小元模型）")
async def flow_template(flow_id: str = Query(...), name: str = Query(""),
                        keyword: str = Query(""),
                        user: CurrentUser = Depends(require_config_editor)):
    from ..flows.template import build_minimal_definition
    return {"definition": build_minimal_definition(flow_id, name, keyword=keyword)}


@router.get("/render-config", summary="前端渲染表单 / 确认卡所需的最小字段集（不含 PII 值）")
async def render_config(flow_id: str = Query(...),
                        user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    return d.render_config()


@router.get("/audit", summary="配置变更审计（管理员）")
async def config_audit(limit: int = 100, admin: CurrentUser = Depends(require_admin)):
    return {"logs": await store.list_audit(limit=limit)}


# ══════════════════ 配置管理 ══════════════════

@router.get("", summary="流程列表")
async def list_flows(user: CurrentUser = Depends(require_config_editor)):
    return {"flows": await store.list_flows()}


@router.post("/bootstrap", summary="初始化入园配置并迁移老数据（幂等，管理员）")
async def bootstrap(admin: CurrentUser = Depends(require_admin)):
    from ..flows.seed import bootstrap as seed_bootstrap
    try:
        return await seed_bootstrap()
    except Exception as e:
        logger.error(f"[flow_bootstrap] 失败: {e}", exc_info=True)
        raise _as_http(e)


@router.get("/{flow_id}", summary="读当前发布版（渲染元数据 + 版本）")
async def get_flow(flow_id: str, user: CurrentUser = Depends(get_current_user)):
    published = await store.get_published(flow_id)
    if published is None:
        raise HTTPException(status_code=404, detail="流程未发布")
    d = FlowDefinition(published["definition"])
    return {"flow_id": flow_id, "version": published["version"],
            "published_by": published["published_by"], "render_config": d.render_config(),
            "state_labels": d.state_labels, "transitions": d.transitions}


@router.get("/{flow_id}/draft", summary="读草稿")
async def get_draft(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return await store.get_draft(flow_id)


@router.put("/{flow_id}/draft", summary="存草稿（乐观锁；revision 不符返回 409）")
async def save_draft(flow_id: str, request: DraftSave,
                     user: CurrentUser = Depends(require_config_editor)):
    try:
        return await store.save_draft(flow_id, request.definition,
                                      expect_revision=request.expect_draft_revision,
                                      actor=user.username, run_validation=True)
    except store.FlowStoreError as e:
        raise HTTPException(status_code=e.status_code, detail=e.message)
    except FlowConfigError as e:
        raise _as_http(e)


@router.post("/{flow_id}/validate", summary="dry-run 八项校验（发布前闸门）")
async def validate_flow(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return await store.validate(flow_id, user.username)


@router.post("/{flow_id}/preview", summary="预览确认卡 / 工具 schema / 菜单")
async def preview_flow(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return await store.preview(flow_id)


@router.post("/{flow_id}/publish", summary="发布（强制双人 + dry-run 全绿）")
async def publish_flow(flow_id: str, request: PublishRequest,
                       publisher: CurrentUser = Depends(require_config_publisher)):
    try:
        return await store.publish(flow_id, actor=publisher.username,
                                   expect_version=request.expect_version, note=request.note)
    except store.FlowStoreError as e:
        raise HTTPException(status_code=e.status_code, detail={"message": e.message, **e.extra})
    except FlowConfigError as e:
        raise _as_http(e)


@router.post("/{flow_id}/rollback", summary="一键回滚到指定版本")
async def rollback_flow(flow_id: str, request: RollbackRequest,
                        publisher: CurrentUser = Depends(require_config_publisher)):
    try:
        return await store.rollback(flow_id, request.version, publisher.username)
    except store.FlowStoreError as e:
        raise _as_http(e)


@router.get("/{flow_id}/versions", summary="版本列表")
async def list_versions(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return {"versions": await store.versions(flow_id)}


@router.get("/{flow_id}/versions/{version}/diff", summary="版本 diff")
async def get_version_diff(flow_id: str, version: int,
                           user: CurrentUser = Depends(require_config_editor)):
    try:
        return await store.version_diff(flow_id, version)
    except store.FlowStoreError as e:
        raise _as_http(e)


@router.get("/{flow_id}/test-cases", summary="意图用例表（DB）")
async def get_test_cases(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return {"cases": (await store.get_draft(flow_id))["definition"].get("intent", {}).get("test_cases") or []}


@router.put("/{flow_id}/test-cases", summary="维护意图用例表")
async def save_test_cases(flow_id: str, request: TestCasesRequest,
                          user: CurrentUser = Depends(require_config_editor)):
    try:
        return await store.save_test_cases(flow_id, request.cases, user.username,
                                           request.expect_draft_revision)
    except store.FlowStoreError as e:
        raise _as_http(e)


@router.post("/{flow_id}/test-cases/run", summary="用例回归（后端跑）")
async def run_test_cases(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return await store.run_test_cases(flow_id, user.username)


@router.get("/{flow_id}/rollout", summary="灰度配置（D13）")
async def get_rollout(flow_id: str, user: CurrentUser = Depends(require_config_editor)):
    return {"rollout": await store.get_rollout(flow_id)}


@router.post("/{flow_id}/rollout", summary="设置灰度 / 全量")
async def set_rollout(flow_id: str, request: RolloutRequest,
                      publisher: CurrentUser = Depends(require_config_publisher)):
    config = request.model_dump()
    return await store.set_rollout(flow_id, config, publisher.username)


@router.get("/{flow_id}/audit", summary="该流程的配置变更审计")
async def flow_audit(flow_id: str, limit: int = 100,
                     user: CurrentUser = Depends(require_config_editor)):
    return {"logs": await store.list_audit(flow_id, limit=limit)}


# ══════════════════ 通用运行时业务 API ══════════════════

@router.post("/{flow_id}/parse", summary="从聊天原文重抽字段（状态恢复，不落库）")
async def parse_flow_text(flow_id: str, request: ParseRequest,
                          user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    extracted = d.extract_by_rules(request.text)
    merged = d.merge_slots(request.slots, extracted)
    return d.build_payload(merged, note="从对话历史重抽")


@router.get("/{flow_id}/meta", summary="表单元数据（配置驱动）")
async def flow_meta(flow_id: str, user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    return {"flow_id": flow_id, "name": d.name, "version": d.version,
            "render_config": d.render_config(), "state_labels": d.state_labels,
            "status_labels": d.state_labels}


@router.post("/{flow_id}/form/validate", summary="提交前双端校验（与提交共用同一套配置规则）")
async def validate_form(flow_id: str, request: FormValidateRequest,
                        user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    return d.validate(request.form_data, for_submit=request.for_submit)


@router.post("/{flow_id}/attachments", summary="上传附件（独立存储，不进知识库）")
async def upload_attachment(flow_id: str, file: UploadFile = File(...),
                            kind: str = Form(...),
                            user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    if kind not in d.attachment_kinds:
        raise HTTPException(status_code=400, detail=f"未知的附件类型: {kind}")
    filename = os.path.basename((file.filename or "unnamed").replace("\\", "/"))
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 JPG / PNG / WEBP / PDF 格式")
    content = await file.read(_MAX_FILE_SIZE + 1)
    if len(content) > _MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="单个附件不能超过 10MB")
    if not content or not _MAGIC[ext](content):
        raise HTTPException(status_code=400, detail="文件内容与扩展名不匹配，或文件为空")
    owner_dir = os.path.join(UPLOAD_ROOT, _safe_component(flow_id), _safe_component(user.username))
    os.makedirs(owner_dir, exist_ok=True)
    storage_path = os.path.join(owner_dir, f"{uuid.uuid4().hex}{ext}")
    if not os.path.abspath(storage_path).startswith(os.path.abspath(UPLOAD_ROOT) + os.sep):
        raise HTTPException(status_code=400, detail="非法的存储路径")
    with open(storage_path, "wb") as f:
        f.write(content)
    att = await svc.save_attachment(flow_id, user.username, kind, filename[:255], storage_path,
                                    file.content_type or "application/octet-stream", len(content))
    return {"id": att["id"], "kind": kind, "label": d.attachment_kinds[kind],
            "filename": filename, "url": f"/api/flows/{flow_id}/attachments/{att['id']}"}


@router.post("/{flow_id}/attachments/mine", summary="列出本人附件（显式 ID，按流程与复用策略过滤）")
async def list_my_attachments(flow_id: str, hours: int = 48,
                              user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    return {"attachments": await svc.list_my_attachments(d, user.username,
                                                         hours=min(hours, 168))}


@router.get("/{flow_id}/attachments/{att_id}", summary="查看 / 下载附件")
async def get_attachment_file(flow_id: str, att_id: str, download: bool = False,
                              user: CurrentUser = Depends(get_current_user)):
    att = await svc.get_attachment(att_id)
    if att is None or att["flow_id"] != flow_id:
        raise HTTPException(status_code=404, detail="附件不存在")
    if att["owner_username"] != user.username and user.role_id != "admin":
        raise HTTPException(status_code=403, detail="无权访问该附件")
    path = att["storage_path"]
    if not os.path.abspath(path).startswith(os.path.abspath(UPLOAD_ROOT) + os.sep):
        raise HTTPException(status_code=400, detail="非法的存储路径")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="附件文件已丢失")
    return FileResponse(path, media_type=att["content_type"] or "application/octet-stream",
                        filename=att["filename"] if download else None)


@router.post("/{flow_id}/applications", summary="创建草稿（手动 / AI 预填共用唯一写入路径）")
async def create_application(flow_id: str, request: ApplicationCreate,
                             user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    _check_applicant(d, user)
    try:
        await svc.validate_attachment_ids(d, user.username, request.form_data)
        return await svc.create_application(d, user.username, request.form_data,
                                            assisted_by_ai=request.assisted_by_ai)
    except FlowError as e:
        raise _as_http(e)


@router.get("/{flow_id}/applications", summary="申请单列表")
async def list_applications(flow_id: str, status: Optional[str] = None,
                            scope: str = "mine", limit: int = 50, offset: int = 0,
                            user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    if scope == "all":
        if user.role_id != "admin":
            raise HTTPException(status_code=403, detail="无权查看全部申请单")
        applicant = None
    elif scope == "mine":
        applicant = user.username
    else:
        raise HTTPException(status_code=400, detail="scope 只能是 mine 或 all")
    try:
        total, items = await svc.list_applications(flow_id=flow_id, applicant=applicant,
                                                   status=status, limit=limit, offset=offset)
    except FlowError as e:
        raise _as_http(e)
    return {"total": total, "applications": items, "status_labels": d.state_labels,
            "flow_id": flow_id, "version": d.version}


@router.get("/{flow_id}/applications/{app_id}", summary="申请单详情")
async def get_application(flow_id: str, app_id: str,
                          user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user, application_version=None)
    return await _visible(d, app_id, user)


@router.get("/{flow_id}/applications/{app_id}/audit", summary="单据流转审计")
async def get_audit(flow_id: str, app_id: str,
                    user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    await _visible(d, app_id, user)
    return {"application_id": app_id, "logs": await svc.list_audit(app_id)}


@router.put("/{flow_id}/applications/{app_id}", summary="编辑草稿（按单据锁定版本）")
async def update_application(flow_id: str, app_id: str, request: ApplicationUpdate,
                             user: CurrentUser = Depends(get_current_user)):
    app = await svc.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="申请单不存在")
    d = await _definition(flow_id, user, application_version=int(app["definition_version"]))
    await _visible(d, app_id, user)
    try:
        await svc.validate_attachment_ids(d, user.username, request.form_data, app_id=app_id)
        return await svc.update_draft(d, app_id, user.username, request.form_data)
    except FlowError as e:
        raise _as_http(e)


@router.post("/{flow_id}/applications/{app_id}/reopen", summary="驳回后改原单重提")
async def reopen_application(flow_id: str, app_id: str,
                             user: CurrentUser = Depends(get_current_user)):
    app = await svc.get_application(app_id)
    d = await _definition(flow_id, user,
                          application_version=int(app["definition_version"]) if app else None)
    await _visible(d, app_id, user)
    try:
        return await svc.reopen_application(d, app_id, user.username)
    except FlowError as e:
        raise _as_http(e)


@router.get("/{flow_id}/applications/{app_id}/pass", summary="审核产出（编号 / 二维码）")
async def get_pass(flow_id: str, app_id: str,
                   user: CurrentUser = Depends(get_current_user)):
    d = await _definition(flow_id, user)
    await _visible(d, app_id, user)
    try:
        return await svc.get_pass(d, app_id)
    except FlowError as e:
        raise _as_http(e)


@router.post("/{flow_id}/applications/{app_id}/submit", summary="本人确认并提交（draft→pending）")
async def submit_application(flow_id: str, app_id: str,
                             user: CurrentUser = Depends(get_current_user)):
    app = await svc.get_application(app_id)
    d = await _definition(flow_id, user,
                          application_version=int(app["definition_version"]) if app else None)
    _check_applicant(d, user)
    try:
        return await svc.submit_application(d, app_id, user.username)
    except FlowError as e:
        raise _as_http(e)


@router.post("/{flow_id}/applications/{app_id}/cancel", summary="撤回申请")
async def cancel_application(flow_id: str, app_id: str,
                             user: CurrentUser = Depends(get_current_user)):
    app = await svc.get_application(app_id)
    d = await _definition(flow_id, user,
                          application_version=int(app["definition_version"]) if app else None)
    try:
        return await svc.cancel_application(d, app_id, user.username)
    except FlowError as e:
        raise _as_http(e)


@router.get("/{flow_id}/review", summary="待审核列表（管理员）")
async def review_list(flow_id: str, status: str = "pending", limit: int = 50, offset: int = 0,
                      admin: CurrentUser = Depends(require_admin)):
    d = await _definition(flow_id, admin)
    if admin.role_id not in d.reviewer_roles and admin.role_id != "admin":
        raise HTTPException(status_code=403, detail="当前角色不是该流程审核人")
    try:
        total, items = await svc.list_applications(flow_id=flow_id, status=status or None,
                                                   limit=limit, offset=offset)
    except FlowError as e:
        raise _as_http(e)
    return {"total": total, "applications": items, "status_labels": d.state_labels}


@router.post("/{flow_id}/applications/{app_id}/review", summary="审核（admin，禁止自审自批）")
async def review_application(flow_id: str, app_id: str, request: ReviewRequest,
                             admin: CurrentUser = Depends(require_admin)):
    app = await svc.get_application(app_id)
    d = await _definition(flow_id, admin,
                          application_version=int(app["definition_version"]) if app else None)
    try:
        return await svc.review_application(d, app_id, admin.username, request.approved,
                                            request.comment, actor_role=admin.role_id)
    except FlowError as e:
        raise _as_http(e)
