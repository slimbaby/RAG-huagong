"""
司机入园预约 —— 数据访问层（方案乙）

Web 手动申请与 AI 对话预填共用这里的所有写入函数，保证「已申请列表」同源。
方案乙里 AI 不写库：agent 只产出字段 JSON，前端预填表单后走同一个提交接口，
assisted_by_ai 仅用于标记「这单是 AI 帮忙填的」，便于统计使用率。

安全约定：
- 归属判断一律放在 SQL 的 WHERE 条件里，不依赖调用方自觉；
- 附件一张只能被一张申请单绑定，杜绝「多次申请复用上一次证件」；
- 提交时做确定性校验（字段单一来源 entry_schema），不交给模型判断。
"""

import base64
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from . import entry_schema as schema

logger = logging.getLogger(__name__)

# 兼容既有引用：字段定义的唯一来源是 entry_schema，这里只做别名导出
ATTACHMENT_KINDS: Dict[str, str] = schema.ATTACHMENT_KINDS
REQUIRED_FIELDS: List[Tuple[str, str]] = schema.REQUIRED_FIELDS
MAX_WEIGHT_TON = schema.MAX_WEIGHT_TON

VALID_STATUSES = ("draft", "pending", "approved", "rejected", "cancelled")


class EntryError(Exception):
    """业务错误，API 层统一转成 HTTPException。"""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _json_load(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    try:
        return dict(value)
    except (ValueError, TypeError):
        return {}


def _json_dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _row_to_dict(row) -> Dict[str, Any]:
    d = dict(row)
    d["form_data"] = _json_load(d.get("form_data"))
    for key in ("entry_time", "exit_time", "reviewed_at", "confirmed_at",
                "pass_issued_at", "created_at", "updated_at"):
        v = d.get(key)
        if v is not None and hasattr(v, "isoformat"):
            d[key] = v.isoformat()
    return d


def new_application_id() -> str:
    return "EN" + datetime.now().strftime("%Y%m%d") + uuid.uuid4().hex[:8].upper()


def new_attachment_id() -> str:
    return "ATT" + uuid.uuid4().hex[:16].upper()


def new_pass_no() -> str:
    return "GP" + datetime.now().strftime("%Y%m%d") + uuid.uuid4().hex[:8].upper()


def missing_fields(form_data: Dict[str, Any]) -> List[str]:
    """返回缺失项的中文标签（委托给字段单一来源）。"""
    return schema.missing_labels(form_data)


def validate_form(form_data: Dict[str, Any], *, for_submit: bool = False) -> Dict[str, Any]:
    """确定性校验：必填 + 重量上限 + 时间先后/格式 + 入园不早于当前。"""
    return schema.validate(form_data, for_submit=for_submit)


def _form_columns(form: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(form.get("driver_name") or "")[:64],
        str(form.get("phone") or "")[:32],
        str(form.get("plate_no") or "")[:32],
        str(form.get("company_name") or "")[:128],
    )


def make_qr_data_uri(payload: str) -> str:
    """把通行证内容编码成二维码 SVG（data URI，前端可直接 <img src=>）。

    qrcode 是纯 Python 依赖；若部署镜像尚未安装该依赖，降级为空字符串，
    接口仍返回编号与原始内容，不至于让审核通过整个失败。
    """
    if not payload:
        return ""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
        from qrcode.image.svg import SvgPathImage

        qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M,
                           box_size=8, border=2)
        qr.add_data(payload)
        qr.make(fit=True)
        img = qr.make_image(image_factory=SvgPathImage)
        raw = img.to_string()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
    except Exception as e:  # pragma: no cover - 依赖缺失时的降级路径
        logger.warning(f"二维码生成失败（不影响通行证编号）: {e}")
        return ""


async def _audit(conn, app_id: str, action: str, actor: str,
                 from_status: str = "", to_status: str = "", detail: Optional[dict] = None):
    await conn.execute(
        """INSERT INTO entry_audit_log
           (application_id, action, actor_username, from_status, to_status, detail)
           VALUES ($1, $2, $3, $4, $5, $6::jsonb)""",
        app_id, action, actor, from_status, to_status, _json_dump(detail),
    )


# ── 申请单 ──

async def create_application(
    applicant: str,
    form_data: Dict[str, Any],
    *,
    assisted_by_ai: bool = False,
) -> Dict[str, Any]:
    """创建草稿申请单。

    方案乙只有这一条写入路径（手动填表 / AI 预填表单都调这里），
    source 固定为 manual，用 assisted_by_ai 区分是否由 Agent 辅助。
    """
    if not applicant:
        raise EntryError("无法确定申请人身份，请重新登录", 401)

    app_id = new_application_id()
    form = schema.sanitize_slots(form_data)
    driver_name, phone, plate_no, company_name = _form_columns(form)

    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO entry_applications
               (id, applicant_username, source, status, form_data,
                driver_name, phone, plate_no, company_name, entry_time, exit_time,
                assisted_by_ai)
               VALUES ($1,$2,'manual','draft',$3::jsonb,$4,$5,$6,$7,$8,$9,$10)
               RETURNING *""",
            app_id, applicant, _json_dump(form),
            driver_name, phone, plate_no, company_name,
            schema.parse_dt(form.get("entry_time")), schema.parse_dt(form.get("exit_time")),
            bool(assisted_by_ai),
        )
        if row is None:
            raise EntryError("申请单创建失败", 500)
        await _audit(conn, app_id, "create", applicant, "", "draft",
                     {"assisted_by_ai": bool(assisted_by_ai)})
    return _row_to_dict(row)


async def get_application(app_id: str) -> Optional[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM entry_applications WHERE id = $1", app_id)
    return _row_to_dict(row) if row else None


async def update_draft(app_id: str, applicant: str, form_data: Dict[str, Any]) -> Dict[str, Any]:
    """仅草稿且仅本人可编辑；归属判断写在 WHERE 里。"""
    form = schema.sanitize_slots(form_data)
    driver_name, phone, plate_no, company_name = _form_columns(form)
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE entry_applications
               SET form_data=$3::jsonb, driver_name=$4, phone=$5, plate_no=$6,
                   company_name=$7, entry_time=$8, exit_time=$9, updated_at=NOW()
               WHERE id=$1 AND applicant_username=$2 AND status='draft'
               RETURNING *""",
            app_id, applicant, _json_dump(form),
            driver_name, phone, plate_no, company_name,
            schema.parse_dt(form.get("entry_time")), schema.parse_dt(form.get("exit_time")),
        )
        if row is None:
            raise EntryError("申请单不存在、已被提交或无权编辑", 404)
        await _audit(conn, app_id, "update", applicant, "draft", "draft")
    return _row_to_dict(row)


async def reopen_application(app_id: str, applicant: str) -> Dict[str, Any]:
    """驳回后改原单重提：rejected → draft，保留同一单号与完整审计链。"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT status FROM entry_applications
                   WHERE id=$1 AND applicant_username=$2 FOR UPDATE""",
                app_id, applicant,
            )
            if row is None:
                raise EntryError("申请单不存在或无权操作", 404)
            if row["status"] != "rejected":
                raise EntryError(f"当前状态为 {row['status']}，只有已驳回的申请可以重提", 409)
            updated = await conn.fetchrow(
                """UPDATE entry_applications
                   SET status='draft', confirmed_at=NULL, reviewed_at=NULL,
                       reviewed_by='', pass_no='', qr_payload='', pass_issued_at=NULL,
                       updated_at=NOW()
                   WHERE id=$1 RETURNING *""",
                app_id,
            )
            await _audit(conn, app_id, "reopen", applicant, "rejected", "draft")
    return _row_to_dict(updated)


async def submit_application(app_id: str, applicant: str) -> Dict[str, Any]:
    """司机确认后调用：draft → pending。

    提交时做三件事：确定性校验（必填/重量/时间）、附件归属与未串单校验、
    把附件绑定到本单。全部在同一事务里完成。
    """
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """SELECT * FROM entry_applications
                   WHERE id=$1 AND applicant_username=$2 FOR UPDATE""",
                app_id, applicant,
            )
            if row is None:
                raise EntryError("申请单不存在或无权操作", 404)
            if row["status"] != "draft":
                raise EntryError(f"当前状态为 {row['status']}，不能重复提交", 409)

            form = _json_load(row["form_data"])
            check = schema.validate(form, for_submit=True)
            problems = check["missing"] + check["errors"]
            if problems:
                raise EntryError("信息不完整或有误：" + "；".join(problems))

            await _check_and_bind_attachments(conn, app_id, applicant, form)

            updated = await conn.fetchrow(
                """UPDATE entry_applications
                   SET status='pending', confirmed_at=NOW(), updated_at=NOW()
                   WHERE id=$1 RETURNING *""",
                app_id,
            )
            await _audit(conn, app_id, "submit", applicant, "draft", "pending",
                         {"assisted_by_ai": bool(row["assisted_by_ai"])})
    return _row_to_dict(updated)


async def _check_and_bind_attachments(conn, app_id: str, owner: str,
                                      form_data: Dict[str, Any]) -> None:
    """提交时校验附件：存在 / 属于本人 / 种类匹配 / 未被别的申请单占用，然后绑定。"""
    attachments = (form_data or {}).get("attachments") or {}
    pairs = [(kind, str(att_id).strip()) for kind, att_id in attachments.items()
             if str(att_id or "").strip()]
    if not pairs:
        # 五类附件必填，missing 校验已拦下；这里是防御性兜底
        raise EntryError("缺少车辆照片与证件")
    ids = [a for _, a in pairs]
    rows = await conn.fetch(
        "SELECT id, owner_username, kind, application_id FROM entry_attachments WHERE id = ANY($1)",
        ids,
    )
    found = {r["id"]: r for r in rows}
    for kind, att_id in pairs:
        r = found.get(att_id)
        if r is None:
            raise EntryError(f"附件 {att_id} 不存在", 400)
        if r["owner_username"] != owner:
            raise EntryError("附件不属于当前账号，请重新上传", 403)
        if kind in ATTACHMENT_KINDS and r["kind"] != kind:
            raise EntryError(f"{ATTACHMENT_KINDS[kind]} 对应的附件类型不匹配", 400)
        bound = r["application_id"] or ""
        if bound and bound != app_id:
            raise EntryError("该照片/证件已被其它申请单使用，请重新上传", 409)
    bound_count = await conn.fetchval(
        """UPDATE entry_attachments SET application_id=$1
           WHERE id = ANY($2) AND owner_username=$3
             AND (application_id = '' OR application_id = $1)""",
        app_id, ids, owner,
    )
    # asyncpg 的 UPDATE 返回 "UPDATE n" 字符串
    logger.info(f"[entry] 绑定附件 app={app_id} result={bound_count} expect={len(set(ids))}")


async def cancel_application(app_id: str, applicant: str) -> Dict[str, Any]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT status FROM entry_applications WHERE id=$1 AND applicant_username=$2 FOR UPDATE",
                app_id, applicant,
            )
            if row is None:
                raise EntryError("申请单不存在或无权操作", 404)
            if row["status"] not in ("draft", "pending"):
                raise EntryError("已审核的申请单不能撤回", 409)
            updated = await conn.fetchrow(
                "UPDATE entry_applications SET status='cancelled', updated_at=NOW() WHERE id=$1 RETURNING *",
                app_id,
            )
            # 解绑附件，司机可以拿同一批照片重新申请
            await conn.execute(
                "UPDATE entry_attachments SET application_id='' WHERE application_id=$1", app_id
            )
            await _audit(conn, app_id, "cancel", applicant, row["status"], "cancelled")
    return _row_to_dict(updated)


async def review_application(app_id: str, admin: str, approved: bool,
                             comment: str = "") -> Dict[str, Any]:
    """admin 审核：pending → approved / rejected；通过时生成通行证编号与二维码内容。"""
    target = "approved" if approved else "rejected"
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM entry_applications WHERE id=$1 FOR UPDATE", app_id
            )
            if row is None:
                raise EntryError("申请单不存在", 404)
            if row["status"] != "pending":
                raise EntryError(f"当前状态为 {row['status']}，不可审核", 409)

            pass_no, qr_payload = "", ""
            if approved:
                for _ in range(5):
                    candidate = new_pass_no()
                    exists = await conn.fetchval(
                        "SELECT 1 FROM entry_applications WHERE pass_no=$1", candidate
                    )
                    if not exists:
                        pass_no = candidate
                        break
                if not pass_no:
                    raise EntryError("通行证编号生成失败，请重试", 500)
                form = _json_load(row["form_data"])
                qr_payload = "|".join([
                    "ENTRY-PASS", pass_no, app_id,
                    str(form.get("plate_no") or ""),
                    str(form.get("driver_name") or ""),
                    str(row["entry_time"] or ""), str(row["exit_time"] or ""),
                ])
                updated = await conn.fetchrow(
                    """UPDATE entry_applications
                       SET status=$2, review_comment=$3, reviewed_by=$4,
                           reviewed_at=NOW(), pass_no=$5, qr_payload=$6,
                           pass_issued_at=NOW(), updated_at=NOW()
                       WHERE id=$1 RETURNING *""",
                    app_id, target, comment or "", admin, pass_no, qr_payload,
                )
            else:
                updated = await conn.fetchrow(
                    """UPDATE entry_applications
                       SET status=$2, review_comment=$3, reviewed_by=$4,
                           reviewed_at=NOW(), updated_at=NOW()
                       WHERE id=$1 RETURNING *""",
                    app_id, target, comment or "", admin,
                )
                # 驳回后允许改原单重提，先解绑附件以便重新提交时再绑定
                await conn.execute(
                    "UPDATE entry_attachments SET application_id='' WHERE application_id=$1",
                    app_id,
                )
            await _audit(conn, app_id, "review", admin, "pending", target,
                         {"comment": comment or "", "pass_no": pass_no})
    return _row_to_dict(updated)


async def get_pass(app_id: str) -> Dict[str, Any]:
    """通行证信息（编号 + 二维码 data URI），供「我的申请」展示。"""
    app = await get_application(app_id)
    if app is None:
        raise EntryError("申请单不存在", 404)
    if app["status"] != "approved" or not app.get("pass_no"):
        raise EntryError("该申请尚未生成通行证", 404)
    return {
        "application_id": app_id,
        "pass_no": app["pass_no"],
        "payload": app.get("qr_payload") or "",
        "issued_at": app.get("pass_issued_at"),
        "qr_svg": make_qr_data_uri(app.get("qr_payload") or ""),
    }


async def list_applications(
    *,
    applicant: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Tuple[int, List[Dict[str, Any]]]:
    """applicant=None 表示跨用户查询（admin 视角）；否则只看自己的单。"""
    where, args = [], []
    if applicant is not None:
        args.append(applicant)
        where.append(f"applicant_username = ${len(args)}")
    if status:
        args.append(status)
        where.append(f"status = ${len(args)}")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM entry_applications{clause}", *args
        )
        args.extend([min(max(limit, 1), 200), max(offset, 0)])
        rows = await conn.fetch(
            f"""SELECT * FROM entry_applications{clause}
                ORDER BY created_at DESC LIMIT ${len(args)-1} OFFSET ${len(args)}""",
            *args,
        )
    return int(total or 0), [_row_to_dict(r) for r in rows]


# ── 附件 ──

async def save_attachment(owner: str, kind: str, filename: str, storage_path: str,
                          content_type: str, size_bytes: int,
                          ocr_text: Optional[dict] = None) -> Dict[str, Any]:
    att_id = new_attachment_id()
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO entry_attachments
               (id, owner_username, kind, filename, storage_path, content_type, size_bytes, ocr_text)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) RETURNING *""",
            att_id, owner, kind, filename, storage_path, content_type,
            size_bytes, _json_dump(ocr_text),
        )
    return _row_to_dict(row)


async def get_attachment(att_id: str) -> Optional[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM entry_attachments WHERE id=$1", att_id)
    return _row_to_dict(row) if row else None


async def list_my_attachments(owner: str, *, hours: int = 48,
                              limit: int = 50) -> List[Dict[str, Any]]:
    """Agent 的「取本人已上传附件」工具用。

    只列出显式 ID 清单，**不自动挑每类最新的一张**——旧版按「账号+24h」
    自动取图会让 A 单证件被 B 单复用。由司机/前端明确选哪张，AI 只搬运 ID。
    """
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, kind, filename, size_bytes, application_id, created_at
               FROM entry_attachments
               WHERE owner_username = $1
                 AND created_at > NOW() - ($2::int * INTERVAL '1 hour')
               ORDER BY created_at DESC LIMIT $3""",
            owner, hours, min(max(limit, 1), 100),
        )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("created_at") is not None and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        d["label"] = ATTACHMENT_KINDS.get(d["kind"], d["kind"])
        d["bound"] = bool(d.get("application_id"))
        out.append(d)
    return out


async def validate_attachment_ids(owner: str, form_data: Dict[str, Any],
                                  app_id: str = "") -> None:
    """创建/编辑草稿时校验：表单引用的附件必须存在、属于本人、种类匹配且未被别的单占用。"""
    attachments = (form_data or {}).get("attachments") or {}
    pairs = [(kind, str(att_id).strip()) for kind, att_id in attachments.items()
             if str(att_id or "").strip()]
    if not pairs:
        return
    ids = [a for _, a in pairs]
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, owner_username, kind, application_id
               FROM entry_attachments WHERE id = ANY($1)""", ids
        )
    found = {r["id"]: r for r in rows}
    for kind, att_id in pairs:
        r = found.get(att_id)
        if r is None:
            raise EntryError(f"附件 {att_id} 不存在", 400)
        if r["owner_username"] != owner:
            raise EntryError("附件不属于当前账号，请重新上传", 403)
        if kind in ATTACHMENT_KINDS and r["kind"] != kind:
            raise EntryError(f"{ATTACHMENT_KINDS[kind]} 对应的附件类型不匹配", 400)
        bound = r["application_id"] or ""
        if bound and bound != app_id:
            raise EntryError("该照片/证件已被其它申请单使用，请重新上传", 409)


async def list_audit(app_id: str) -> List[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM entry_audit_log WHERE application_id=$1 ORDER BY created_at, id", app_id
        )
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = _json_load(d.get("detail"))
        if d.get("created_at") is not None and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
