"""
司机入园预约 API

链路：
  司机 Web 手动申请 / RAG 对话 Agent 预填表单
    → entry_applications(draft)
    → 司机确认提交 (pending)
    → admin 待审核页审核 (approved / rejected)
    → 通过时生成通行证编号 + 二维码；驳回可改原单重提

安全约定：
- 申请人身份只从 JWT 上下文取，接口不接受 applicant 参数；
- 附件走独立目录与独立表，绝不写入知识库 / Milvus；
- 所有归属判断在 service 层 SQL WHERE 中完成；
- 审核接口依赖 require_admin 做后端硬校验。
"""

import logging
import os
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .auth import CurrentUser, get_current_user, require_admin
# 阶段 1 后：老三表已迁移为通用表 + 只读兼容视图，写入统一转发到通用 service。
# 保留全部函数签名与响应结构，旧前端 / E2E 无需改动。
from ..services import entry_compat as svc
from ..services import entry_compat as schema

logger = logging.getLogger(__name__)
router = APIRouter()

UPLOAD_ROOT = "./uploads/entry"
_MAX_FILE_SIZE = 10 * 1024 * 1024  # 单张 10MB
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}
_MAGIC = {
    ".jpg": lambda b: b[:3] == b"\xff\xd8\xff",
    ".jpeg": lambda b: b[:3] == b"\xff\xd8\xff",
    ".png": lambda b: b[:8] == b"\x89PNG\r\n\x1a\n",
    ".webp": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WEBP",
    ".pdf": lambda b: b[:5] == b"%PDF-",
}

STATUS_LABELS = {
    "draft": "草稿（待确认提交）",
    "pending": "待审核",
    "approved": "审核通过",
    "rejected": "已驳回",
    "cancelled": "已撤回",
}


def _safe_component(value: str) -> str:
    """路径组件清洗：只允许字母数字下划线连字符，阻断 ../ 逃逸。"""
    return re.sub(r"[^A-Za-z0-9_-]", "_", value or "")[:64] or "unknown"


class ApplicationCreate(BaseModel):
    form_data: Dict[str, Any] = Field(default_factory=dict)
    # 由聊天确认卡 / 预填表单带入：标记「这单是 AI 帮忙填的」，不改写入路径
    assisted_by_ai: bool = False


class ApplicationUpdate(BaseModel):
    form_data: Dict[str, Any]


class ReviewRequest(BaseModel):
    approved: bool
    comment: str = ""


class ValidateRequest(BaseModel):
    form_data: Dict[str, Any] = Field(default_factory=dict)
    for_submit: bool = True


class ParseRequest(BaseModel):
    """前端刷新/换设备后，把聊天历史原文送回来重抽全量字段（不落库）。"""
    text: str = ""
    slots: Dict[str, Any] = Field(default_factory=dict)


@router.post("/parse", summary="从聊天原文重抽入园字段（状态恢复用，不落库）")
async def parse_entry_text(request: ParseRequest, user: CurrentUser = Depends(get_current_user)):
    extracted = schema.extract_by_rules(request.text)
    merged = schema.merge_slots(request.slots, extracted)
    return schema.build_entry_payload(merged, note="从对话历史重抽")


@router.get("/meta", summary="入园申请表单元数据")
async def entry_meta():
    """前端/Agent 据此渲染字段与附件项，保证前后端只有一份字段定义。"""
    return {
        "attachment_kinds": [
            {"key": k, "label": v} for k, v in schema.ATTACHMENT_KINDS.items()
        ],
        "required_fields": [
            {"key": k, "label": v} for k, v in schema.REQUIRED_FIELDS
        ],
        "fields": schema.FORM_FIELDS,
        "field_groups": schema.FIELD_GROUPS,
        "max_weight_ton": schema.MAX_WEIGHT_TON,
        "status_labels": STATUS_LABELS,
    }


@router.post("/validate", summary="校验入园表单（前端提交前双端校验）")
async def validate_form(request: ValidateRequest):
    """与提交接口共用同一套确定性规则，避免前后端各写一套逐渐漂移。"""
    return schema.validate(request.form_data, for_submit=request.for_submit)


# ── 附件 ──

@router.post("/attachments", summary="上传入园申请附件（独立存储，不进知识库）")
async def upload_attachment(
    file: UploadFile = File(...),
    kind: str = Form(...),
    user: CurrentUser = Depends(get_current_user),
):
    if kind not in svc.ATTACHMENT_KINDS:
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

    owner_dir = os.path.join(UPLOAD_ROOT, _safe_component(user.username))
    os.makedirs(owner_dir, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    storage_path = os.path.join(owner_dir, stored_name)
    # 二次校验：确保最终路径没有逃逸出附件根目录
    if not os.path.abspath(storage_path).startswith(os.path.abspath(UPLOAD_ROOT) + os.sep):
        raise HTTPException(status_code=400, detail="非法的存储路径")
    with open(storage_path, "wb") as f:
        f.write(content)

    att = await svc.save_attachment(
        owner=user.username,
        kind=kind,
        filename=filename[:255],
        storage_path=storage_path,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(content),
    )
    return {
        "id": att["id"],
        "kind": kind,
        "label": svc.ATTACHMENT_KINDS[kind],
        "filename": filename,
        "url": f"/api/entry/attachments/{att['id']}",
    }


@router.post("/attachments/mine", summary="列出本人最近上传的附件（显式 ID，不自动取图）")
async def list_my_attachments(
    hours: int = 48,
    user: CurrentUser = Depends(get_current_user),
):
    """供前端 / Agent 明确挑选附件 ID；旧版「自动取每类最新一张」会串图，已废弃。"""
    return {"attachments": await svc.list_my_attachments(user.username, hours=min(hours, 168))}


@router.get("/attachments/{att_id}", summary="查看或下载附件")
async def get_attachment_file(
    att_id: str,
    download: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    att = await svc.get_attachment(att_id)
    if att is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    if att["owner_username"] != user.username and user.role_id != "admin":
        raise HTTPException(status_code=403, detail="无权访问该附件")

    path = att["storage_path"]
    if not os.path.abspath(path).startswith(os.path.abspath(UPLOAD_ROOT) + os.sep):
        raise HTTPException(status_code=400, detail="非法的存储路径")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="附件文件已丢失")
    return FileResponse(
        path,
        media_type=att["content_type"] or "application/octet-stream",
        filename=att["filename"] if download else None,
    )


# ── 申请单（司机侧）──

@router.post("/applications", summary="创建入园申请草稿（手动填表 / AI 预填表单）")
async def create_application(
    request: ApplicationCreate,
    user: CurrentUser = Depends(get_current_user),
):
    await svc.validate_attachment_ids(user.username, request.form_data)
    return await svc.create_application(
        applicant=user.username,
        form_data=request.form_data,
        assisted_by_ai=request.assisted_by_ai,
    )


@router.get("/applications", summary="申请单列表")
async def list_applications(
    status: Optional[str] = None,
    scope: str = "mine",
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(get_current_user),
):
    """默认只看本人的单（含 AI 生成的草稿）；admin 可用 scope=all 看全部。"""
    if status and status not in svc.VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知状态: {status}")
    if scope == "all":
        if user.role_id != "admin":
            raise HTTPException(status_code=403, detail="无权查看全部申请单")
        applicant = None
    elif scope == "mine":
        applicant = user.username
    else:
        raise HTTPException(status_code=400, detail="scope 只能是 mine 或 all")
    total, items = await svc.list_applications(
        applicant=applicant, status=status, limit=limit, offset=offset
    )
    return {"total": total, "applications": items, "status_labels": STATUS_LABELS}


async def _visible_application(app_id: str, user: CurrentUser) -> Dict[str, Any]:
    app = await svc.get_application(app_id)
    if app is None:
        raise HTTPException(status_code=404, detail="申请单不存在")
    if app["applicant_username"] != user.username and user.role_id != "admin":
        raise HTTPException(status_code=403, detail="无权访问该申请单")
    return app


@router.get("/applications/{app_id}", summary="申请单详情")
async def get_application(app_id: str, user: CurrentUser = Depends(get_current_user)):
    return await _visible_application(app_id, user)


@router.get("/applications/{app_id}/audit", summary="申请单流转审计日志")
async def get_audit_log(app_id: str, user: CurrentUser = Depends(get_current_user)):
    await _visible_application(app_id, user)
    return {"application_id": app_id, "logs": await svc.list_audit(app_id)}


@router.put("/applications/{app_id}", summary="编辑申请草稿")
async def update_application(
    app_id: str,
    request: ApplicationUpdate,
    user: CurrentUser = Depends(get_current_user),
):
    await _visible_application(app_id, user)
    await svc.validate_attachment_ids(user.username, request.form_data, app_id=app_id)
    return await svc.update_draft(app_id, user.username, request.form_data)


@router.post("/applications/{app_id}/reopen", summary="驳回后改原单重提（rejected → draft）")
async def reopen_application(app_id: str, user: CurrentUser = Depends(get_current_user)):
    await _visible_application(app_id, user)
    return await svc.reopen_application(app_id, user.username)


@router.get("/applications/{app_id}/pass", summary="通行证编号与二维码")
async def get_pass(app_id: str, user: CurrentUser = Depends(get_current_user)):
    await _visible_application(app_id, user)
    return await svc.get_pass(app_id)


@router.post("/applications/{app_id}/submit", summary="司机确认并提交申请")
async def submit_application(app_id: str, user: CurrentUser = Depends(get_current_user)):
    """手动填表与 AI 预填的唯一合流点：确定性校验 + 附件绑定 + draft → pending。"""
    return await svc.submit_application(app_id, user.username)


@router.post("/applications/{app_id}/cancel", summary="撤回申请")
async def cancel_application(app_id: str, user: CurrentUser = Depends(get_current_user)):
    return await svc.cancel_application(app_id, user.username)


# ── 审核侧（admin）──

@router.get("/review", summary="待审核申请列表（管理员）")
async def review_list(
    status: str = "pending",
    limit: int = 50,
    offset: int = 0,
    admin: CurrentUser = Depends(require_admin),
):
    if status and status not in svc.VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"未知状态: {status}")
    total, items = await svc.list_applications(
        status=status or None, limit=limit, offset=offset
    )
    return {"total": total, "applications": items, "status_labels": STATUS_LABELS}


@router.post("/applications/{app_id}/review", summary="审核入园申请（管理员）")
async def review_application(
    app_id: str,
    request: ReviewRequest,
    admin: CurrentUser = Depends(require_admin),
):
    return await svc.review_application(
        app_id, admin.username, request.approved, request.comment
    )
