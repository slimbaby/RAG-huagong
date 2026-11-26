"""入园预约兼容层（阶段 1 之后）

老三表已迁移为 flow_* 通用表 + 同名只读兼容视图，原 entry_service 的写操作
不能再落到视图上。这里保留 /api/entry/* 与旧前端 / E2E 的全部函数签名，
把调用转发到通用 service（按 driver_entry 发布版配置），行为保持一致。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..flows import service as flow_service
from ..flows.model import FlowDefinition
from ..flows.registry import get_flow_registry
from ..flows.seed import SEED_FLOW_ID, build_driver_entry_definition

# 同步常量：字段定义的唯一来源是第一份流程配置
_DEFINITION = FlowDefinition(build_driver_entry_definition(cases=[]))

ATTACHMENT_KINDS = _DEFINITION.attachment_kinds
REQUIRED_FIELDS = [(str(f["key"]), str(f["label"])) for f in _DEFINITION.fields]
FORM_FIELDS = _DEFINITION.fields
FIELD_GROUPS = _DEFINITION.groups
MAX_WEIGHT_TON = 100.0
VALID_STATUSES = ("draft", "pending", "approved", "rejected", "cancelled")
STATUS_LABELS = _DEFINITION.state_labels


from .entry_service import EntryError as _BaseEntryError


class EntryError(_BaseEntryError):
    """继承原异常类型，main.py 已有的 exception_handler 可直接复用。"""


def _translate(exc: Exception):
    if isinstance(exc, flow_service.FlowError):
        return EntryError(exc.message, exc.status_code)
    return exc


async def _definition() -> FlowDefinition:
    reg = get_flow_registry()
    await reg.load()
    d = reg.get(SEED_FLOW_ID)
    if d is None:
        d = FlowDefinition(build_driver_entry_definition(cases=[]))
        d.raw.setdefault("meta", {})["version"] = 1
    return d


# ── schema 兼容 ──

def sanitize_slots(payload: Any) -> Dict[str, Any]:
    return _DEFINITION.sanitize_slots(payload)


def merge_slots(base: Any, updates: Any) -> Dict[str, Any]:
    return _DEFINITION.merge_slots(base, updates)


def missing_labels(form_data: Any) -> List[str]:
    return _DEFINITION.missing_labels(form_data)


def validate(form_data: Any, *, for_submit: bool = False, now=None) -> Dict[str, Any]:
    return _DEFINITION.validate(form_data, for_submit=for_submit)


def extract_by_rules(text: str) -> Dict[str, Any]:
    return _DEFINITION.extract_by_rules(text)


def build_card(slots: Any, *, masked: bool = True) -> Dict[str, Any]:
    return _DEFINITION.build_card(slots, masked=masked)


def build_entry_payload(slots: Any, *, for_submit: bool = False,
                        note: str = "") -> Dict[str, Any]:
    return _DEFINITION.build_payload(slots, for_submit=for_submit, note=note)


def llm_slots_schema() -> Dict[str, Any]:
    return _DEFINITION.llm_slots_schema()


def describe_fields_for_prompt() -> str:
    return _DEFINITION.describe_fields_for_prompt()


def to_json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


# ── service 兼容 ──

async def create_application(applicant: str, form_data: Dict[str, Any], *,
                             assisted_by_ai: bool = False) -> Dict[str, Any]:
    try:
        return await flow_service.create_application(
            await _definition(), applicant, form_data, assisted_by_ai=assisted_by_ai)
    except Exception as e:
        raise _translate(e)


async def get_application(app_id: str) -> Optional[Dict[str, Any]]:
    return await flow_service.get_application(app_id)


async def update_draft(app_id: str, applicant: str, form_data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return await flow_service.update_draft(await _definition(), app_id, applicant, form_data)
    except Exception as e:
        raise _translate(e)


async def reopen_application(app_id: str, applicant: str) -> Dict[str, Any]:
    try:
        return await flow_service.reopen_application(await _definition(), app_id, applicant)
    except Exception as e:
        raise _translate(e)


async def submit_application(app_id: str, applicant: str) -> Dict[str, Any]:
    try:
        return await flow_service.submit_application(await _definition(), app_id, applicant)
    except Exception as e:
        raise _translate(e)


async def cancel_application(app_id: str, applicant: str) -> Dict[str, Any]:
    try:
        return await flow_service.cancel_application(await _definition(), app_id, applicant)
    except Exception as e:
        raise _translate(e)


async def review_application(app_id: str, admin: str, approved: bool,
                             comment: str = "") -> Dict[str, Any]:
    try:
        return await flow_service.review_application(
            await _definition(), app_id, admin, approved, comment, actor_role="admin")
    except Exception as e:
        raise _translate(e)


async def get_pass(app_id: str) -> Dict[str, Any]:
    try:
        return await flow_service.get_pass(await _definition(), app_id)
    except Exception as e:
        raise _translate(e)


async def list_applications(*, applicant: Optional[str] = None,
                            status: Optional[str] = None, limit: int = 50,
                            offset: int = 0) -> Tuple[int, List[Dict[str, Any]]]:
    try:
        return await flow_service.list_applications(
            flow_id=SEED_FLOW_ID, applicant=applicant, status=status,
            limit=limit, offset=offset)
    except Exception as e:
        raise _translate(e)


async def save_attachment(owner: str, kind: str, filename: str, storage_path: str,
                          content_type: str, size_bytes: int,
                          ocr_text: Optional[dict] = None) -> Dict[str, Any]:
    return await flow_service.save_attachment(
        SEED_FLOW_ID, owner, kind, filename, storage_path, content_type, size_bytes)


async def get_attachment(att_id: str) -> Optional[Dict[str, Any]]:
    return await flow_service.get_attachment(att_id)


async def list_my_attachments(owner: str, *, hours: int = 48,
                              limit: int = 50) -> List[Dict[str, Any]]:
    return await flow_service.list_my_attachments(await _definition(), owner,
                                                  hours=hours, limit=limit)


async def validate_attachment_ids(owner: str, form_data: Dict[str, Any],
                                  app_id: str = "") -> None:
    try:
        await flow_service.validate_attachment_ids(
            await _definition(), owner, form_data, app_id)
    except Exception as e:
        raise _translate(e)


async def list_audit(app_id: str) -> List[Dict[str, Any]]:
    return await flow_service.list_audit(app_id)
