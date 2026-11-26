"""
通用运行时业务 API 服务层（docs/10 §5.3 / §8.2 / §3.6）

框架硬约束（配置不可关闭）：
- AI 全程只读，确认后由这里写库；submit 只能 draft→pending，pending 只能由 review 离开；
- review 强制 admin 级，且同一账号不能自审自批；
- 附件显式 ID + application_id 绑定，按 reuse_policy 决定复用范围；
- 每条单据锁定 definition_version，按当时的字段与规则复现；
- PII 打码在确认卡 / trace / 日志，审计 detail 不含原始 PII 值。
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import hooks as hook_registry
from .model import ADMIN_LEVEL_ROLES, FRAMEWORK_STATES, FlowDefinition

logger = logging.getLogger(__name__)


class FlowError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _load(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value


def _dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    for key in ("form_data", "attachments", "result"):
        d[key] = _load(d.get(key))
    for key in ("reviewed_at", "confirmed_at", "created_at", "updated_at"):
        v = d.get(key)
        if v is not None and hasattr(v, "isoformat"):
            d[key] = v.isoformat()
    return d


def new_application_id(flow_id: str) -> str:
    prefix = "FL" + "".join(ch for ch in flow_id.upper() if ch.isalnum())[:2]
    return prefix + datetime.now().strftime("%Y%m%d") + uuid.uuid4().hex[:8].upper()


async def _audit(conn, flow_id: str, app_id: str, action: str, actor: str,
                 from_status: str = "", to_status: str = "",
                 detail: Optional[Dict[str, Any]] = None) -> None:
    await conn.execute(
        """INSERT INTO flow_audit_log
           (flow_id, application_id, action, actor, from_status, to_status, detail)
           VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
        flow_id, app_id, action, actor, from_status, to_status, _dump(detail),
    )


def _title(definition: FlowDefinition, form: Dict[str, Any]) -> str:
    key = str(definition.card.get("title_field") or "")
    if key and form.get(key):
        return str(form[key])[:128]
    for f in definition.fields:
        if form.get(f["key"]):
            return str(form[f["key"]])[:128]
    return definition.name


# ── 申请单 ──

async def create_application(definition: FlowDefinition, applicant: str,
                             form_data: Dict[str, Any], *,
                             assisted_by_ai: bool = False) -> Dict[str, Any]:
    if not applicant:
        raise FlowError("无法确定申请人身份，请重新登录", 401)
    app_id = new_application_id(definition.flow_id)
    form = definition.sanitize_slots(form_data)
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO flow_applications
               (id, flow_id, definition_version, status, applicant_username, source,
                assisted_by_ai, form_data, attachments, result, title)
               VALUES ($1,$2,$3,'draft',$4,'manual',$5,$6::jsonb,$7::jsonb,'{}'::jsonb,$8)
               RETURNING *""",
            app_id, definition.flow_id, int(definition.version or 1), applicant,
            bool(assisted_by_ai), _dump(form), _dump(form.get("attachments") or {}),
            _title(definition, form),
        )
        await _audit(conn, definition.flow_id, app_id, "create", applicant, "", "draft",
                     {"assisted_by_ai": bool(assisted_by_ai), "definition_version": definition.version})
    return _row_to_dict(row)


async def get_application(app_id: str) -> Optional[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM flow_applications WHERE id=$1", app_id)
    return _row_to_dict(row) if row else None


async def update_draft(definition: FlowDefinition, app_id: str, applicant: str,
                       form_data: Dict[str, Any]) -> Dict[str, Any]:
    form = definition.sanitize_slots(form_data)
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE flow_applications
               SET form_data=$3::jsonb, attachments=$4::jsonb, title=$5, updated_at=NOW()
               WHERE id=$1 AND applicant_username=$2 AND status='draft'
               RETURNING *""",
            app_id, applicant, _dump(form), _dump(form.get("attachments") or {}),
            _title(definition, form),
        )
        if row is None:
            raise FlowError("申请单不存在、已被提交或无权编辑", 404)
        await _audit(conn, definition.flow_id, app_id, "update", applicant, "draft", "draft")
    return _row_to_dict(row)


async def submit_application(definition: FlowDefinition, app_id: str,
                             applicant: str) -> Dict[str, Any]:
    """确认后调用：确定性校验 + 附件绑定 + draft→pending（唯一合流点）。"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT * FROM flow_applications
                   WHERE id=$1 AND applicant_username=$2 AND flow_id=$3 FOR UPDATE""",
                app_id, applicant, definition.flow_id,
            )
            if row is None:
                raise FlowError("申请单不存在或无权操作", 404)
            if row["status"] != "draft":
                raise FlowError(f"当前状态为 {row['status']}，不能重复提交", 409)
            if definition.find_transition("submit", "draft", "pending") is None:
                raise FlowError("该流程未配置 submit 流转", 409)
            form = _load(row["form_data"])
            check = definition.validate(form, for_submit=True)
            problems = check["missing"] + check["errors"]
            if problems:
                raise FlowError("信息不完整或有误：" + "；".join(problems))
            await _check_and_bind_attachments(conn, definition, app_id, applicant, form)
            updated = await conn.fetchrow(
                """UPDATE flow_applications
                   SET status='pending', confirmed_at=NOW(), updated_at=NOW()
                   WHERE id=$1 RETURNING *""",
                app_id,
            )
            await _audit(conn, definition.flow_id, app_id, "submit", applicant, "draft", "pending",
                         {"assisted_by_ai": bool(row["assisted_by_ai"]),
                          "definition_version": row["definition_version"]})
    return _row_to_dict(updated)


async def _check_and_bind_attachments(conn, definition: FlowDefinition, app_id: str,
                                      owner: str, form: Dict[str, Any]) -> None:
    atts = form.get("attachments") or {}
    pairs = [(str(k), str(v).strip()) for k, v in atts.items() if str(v or "").strip()]
    if not pairs and any(a.get("required") for a in definition.attachments):
        raise FlowError("缺少必交附件")
    if not pairs:
        return
    ids = [a for _, a in pairs]
    rows = await conn.fetch(
        "SELECT id, flow_id, owner_username, kind, application_id "
        "FROM flow_attachments WHERE id = ANY($1)", ids,
    )
    found = {r["id"]: r for r in rows}
    for kind, att_id in pairs:
        r = found.get(att_id)
        if r is None:
            raise FlowError(f"附件 {att_id} 不存在", 400)
        if r["owner_username"] != owner:
            raise FlowError("附件不属于当前账号，请重新上传", 403)
        policy = str((definition.attachment_by_key.get(kind) or {}).get("reuse_policy") or "per_application")
        if policy == "per_application" and r["flow_id"] != definition.flow_id:
            raise FlowError("附件不属于当前流程，请重新上传", 403)
        if kind in definition.attachment_kinds and r["kind"] != kind:
            raise FlowError(f"{definition.attachment_kinds[kind]} 对应的附件类型不匹配", 400)
        bound = r["application_id"] or ""
        if policy == "per_application" and bound and bound != app_id:
            raise FlowError("该照片/证件已被其它申请单使用，请重新上传", 409)
    if any(str((definition.attachment_by_key.get(k) or {}).get("reuse_policy") or "per_application")
           == "per_application" for k, _ in pairs):
        await conn.execute(
            """UPDATE flow_attachments SET application_id=$1
               WHERE id = ANY($2) AND owner_username=$3
                 AND (application_id = '' OR application_id = $1)""",
            app_id, ids, owner,
        )


async def cancel_application(definition: FlowDefinition, app_id: str,
                             applicant: str) -> Dict[str, Any]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT status FROM flow_applications
                   WHERE id=$1 AND applicant_username=$2 AND flow_id=$3 FOR UPDATE""",
                app_id, applicant, definition.flow_id,
            )
            if row is None:
                raise FlowError("申请单不存在或无权操作", 404)
            status = row["status"]
            if definition.find_transition("cancel", status, "cancelled") is None:
                raise FlowError("当前状态不允许撤回", 409)
            updated = await conn.fetchrow(
                "UPDATE flow_applications SET status='cancelled', updated_at=NOW() WHERE id=$1 RETURNING *",
                app_id,
            )
            await conn.execute(
                "UPDATE flow_attachments SET application_id='' WHERE application_id=$1", app_id,
            )
            await _audit(conn, definition.flow_id, app_id, "cancel", applicant, status, "cancelled")
    return _row_to_dict(updated)


async def reopen_application(definition: FlowDefinition, app_id: str,
                             applicant: str) -> Dict[str, Any]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT status FROM flow_applications
                   WHERE id=$1 AND applicant_username=$2 AND flow_id=$3 FOR UPDATE""",
                app_id, applicant, definition.flow_id,
            )
            if row is None:
                raise FlowError("申请单不存在或无权操作", 404)
            if row["status"] != "rejected" or definition.find_transition("reopen", "rejected", "draft") is None:
                raise FlowError(f"当前状态为 {row['status']}，只有已驳回且配置了重提流转的申请可以重提", 409)
            updated = await conn.fetchrow(
                """UPDATE flow_applications
                   SET status='draft', confirmed_at=NULL, reviewed_at=NULL, reviewed_by='',
                       result='{}'::jsonb, updated_at=NOW()
                   WHERE id=$1 RETURNING *""",
                app_id,
            )
            await conn.execute(
                "UPDATE flow_attachments SET application_id='' WHERE application_id=$1", app_id,
            )
            await _audit(conn, definition.flow_id, app_id, "reopen", applicant, "rejected", "draft")
    return _row_to_dict(updated)


async def review_application(definition: FlowDefinition, app_id: str, admin: str,
                             approved: bool, comment: str = "",
                             *, actor_role: str = "admin") -> Dict[str, Any]:
    """admin 审核：pending → approved / rejected；通过时跑 on_review hook 写 result。"""
    target = "approved" if approved else "rejected"
    if actor_role not in ADMIN_LEVEL_ROLES:
        raise FlowError("审核动作必须由 admin 级角色执行", 403)
    transition = definition.find_transition("review", "pending", target)
    if transition is None:
        raise FlowError("该流程未配置人工审批流转，拒绝审核", 409)
    if str(transition.get("actor_role") or "") not in ADMIN_LEVEL_ROLES:
        raise FlowError("审批流转的 actor_role 不是 admin 级，拒绝审核", 409)

    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT * FROM flow_applications
                   WHERE id=$1 AND flow_id=$2 FOR UPDATE""",
                app_id, definition.flow_id,
            )
            if row is None:
                raise FlowError("申请单不存在", 404)
            if row["status"] != "pending":
                raise FlowError(f"当前状态为 {row['status']}，不可审核", 409)
            if row["applicant_username"] == admin:
                raise FlowError("同一账号不能既申请又审核（禁止自审自批）", 403)
            form = _load(row["form_data"])
            result: Dict[str, Any] = {}
            if approved:
                result = hook_registry.run_review_hooks(definition, {
                    "application": dict(row), "form_data": form, "approved": True,
                })
            updated = await conn.fetchrow(
                """UPDATE flow_applications
                   SET status=$2, review_comment=$3, reviewed_by=$4, reviewed_at=NOW(),
                       result=$5::jsonb, updated_at=NOW()
                   WHERE id=$1 RETURNING *""",
                app_id, target, comment or "", admin, _dump(result),
            )
            if not approved:
                await conn.execute(
                    "UPDATE flow_attachments SET application_id='' WHERE application_id=$1", app_id,
                )
            await _audit(conn, definition.flow_id, app_id, "review", admin, "pending", target,
                         {"comment": comment or "", "pass_no": result.get("pass_no", "")})
    return _row_to_dict(updated)


async def get_pass(definition: FlowDefinition, app_id: str) -> Dict[str, Any]:
    app = await get_application(app_id)
    if app is None or app["flow_id"] != definition.flow_id:
        raise FlowError("申请单不存在", 404)
    if app["status"] != "approved" or not (app.get("result") or {}).get("pass_no"):
        raise FlowError("该申请尚未生成通行证", 404)
    result = app["result"]
    return {"application_id": app_id, "flow_id": definition.flow_id,
            "pass_no": result.get("pass_no"), "payload": result.get("qr_payload") or "",
            "issued_at": result.get("pass_issued_at"), "qr_svg": result.get("qr_svg") or ""}


async def list_applications(*, flow_id: str = "", applicant: Optional[str] = None,
                            status: Optional[str] = None, limit: int = 50,
                            offset: int = 0) -> Tuple[int, List[Dict[str, Any]]]:
    where, args = [], []
    if flow_id:
        args.append(flow_id)
        where.append(f"flow_id = ${len(args)}")
    if applicant is not None:
        args.append(applicant)
        where.append(f"applicant_username = ${len(args)}")
    if status:
        if status not in FRAMEWORK_STATES:
            raise FlowError(f"未知状态: {status}", 400)
        args.append(status)
        where.append(f"status = ${len(args)}")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(f"SELECT COUNT(*) FROM flow_applications{clause}", *args)
        args.extend([min(max(limit, 1), 200), max(offset, 0)])
        rows = await conn.fetch(
            f"""SELECT * FROM flow_applications{clause}
                ORDER BY created_at DESC LIMIT ${len(args)-1} OFFSET ${len(args)}""",
            *args,
        )
    return int(total or 0), [_row_to_dict(r) for r in rows]


async def list_audit(app_id: str) -> List[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM flow_audit_log WHERE application_id=$1 ORDER BY created_at, id", app_id,
        )
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = _load(d.get("detail"))
        if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


# ── 附件 ──

async def save_attachment(flow_id: str, owner: str, kind: str, filename: str,
                          storage_path: str, content_type: str, size_bytes: int) -> Dict[str, Any]:
    att_id = "ATT" + uuid.uuid4().hex[:16].upper()
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO flow_attachments
               (id, flow_id, owner_username, kind, filename, storage_path, content_type, size_bytes)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING *""",
            att_id, flow_id, owner, kind, filename, storage_path, content_type, size_bytes,
        )
    return _row_to_dict(row)


async def get_attachment(att_id: str) -> Optional[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM flow_attachments WHERE id=$1", att_id)
    return _row_to_dict(row) if row else None


async def list_my_attachments(definition: FlowDefinition, owner: str, *,
                              hours: int = 48, limit: int = 50) -> List[Dict[str, Any]]:
    """按 flow_id + reuse_policy 过滤；只列显式 ID，绝不自动取「每类最新一张」。"""
    policies = {str(a["key"]): str(a.get("reuse_policy") or "per_application")
                for a in definition.attachments}
    reusable = all(p != "per_application" for p in policies.values()) and bool(policies)
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        if reusable:
            rows = await conn.fetch(
                """SELECT id, flow_id, kind, filename, size_bytes, application_id, created_at
                   FROM flow_attachments WHERE owner_username=$1
                     AND created_at > NOW() - ($2::int * INTERVAL '1 hour')
                   ORDER BY created_at DESC LIMIT $3""",
                owner, min(max(hours, 1), 168), min(max(limit, 1), 100),
            )
        else:
            rows = await conn.fetch(
                """SELECT id, flow_id, kind, filename, size_bytes, application_id, created_at
                   FROM flow_attachments WHERE owner_username=$1 AND flow_id=$2
                     AND created_at > NOW() - ($3::int * INTERVAL '1 hour')
                   ORDER BY created_at DESC LIMIT $4""",
                owner, definition.flow_id, min(max(hours, 1), 168), min(max(limit, 1), 100),
            )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        d["label"] = definition.attachment_kinds.get(d["kind"], d["kind"])
        d["bound"] = bool(d.get("application_id"))
        out.append(d)
    return out


async def validate_attachment_ids(definition: FlowDefinition, owner: str,
                                  form_data: Dict[str, Any], app_id: str = "") -> None:
    atts = (form_data or {}).get("attachments") or {}
    pairs = [(str(k), str(v).strip()) for k, v in atts.items() if str(v or "").strip()]
    if not pairs:
        return
    ids = [a for _, a in pairs]
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, flow_id, owner_username, kind, application_id FROM flow_attachments WHERE id = ANY($1)",
            ids,
        )
    found = {r["id"]: r for r in rows}
    for kind, att_id in pairs:
        r = found.get(att_id)
        if r is None:
            raise FlowError(f"附件 {att_id} 不存在", 400)
        if r["owner_username"] != owner:
            raise FlowError("附件不属于当前账号，请重新上传", 403)
        policy = str((definition.attachment_by_key.get(kind) or {}).get("reuse_policy") or "per_application")
        if policy == "per_application" and r["flow_id"] != definition.flow_id:
            raise FlowError("附件不属于当前流程，请重新上传", 403)
        if kind in definition.attachment_kinds and r["kind"] != kind:
            raise FlowError(f"{definition.attachment_kinds[kind]} 对应的附件类型不匹配", 400)
        bound = r["application_id"] or ""
        if policy == "per_application" and bound and bound != app_id:
            raise FlowError("该照片/证件已被其它申请单使用，请重新上传", 409)


async def has_active_application(flow_id: str, applicant: str) -> bool:
    """该用户在该流程是否已有「已提交且在途」的单据（只读，供意图路由收敛）。

    pending / approved 视为办结：提交后会话不再靠 session_keep 续留办理通道。
    draft / rejected / cancelled 不算，用户仍需继续补材料或重提。
    """
    if not flow_id or not applicant:
        return False
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return bool(await conn.fetchval(
            """SELECT 1 FROM flow_applications
               WHERE flow_id = $1 AND applicant_username = $2
                 AND status IN ('pending', 'approved') LIMIT 1""",
            flow_id, applicant,
        ))
