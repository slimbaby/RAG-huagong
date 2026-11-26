"""
入园申请 Agent —— 只读四件套工具（方案乙）

严格无写权限：四个工具都不 INSERT/UPDATE 任何业务表。Agent 的职责是
「识别 → 解析材料 → 校验规则 → 推确认卡」，最终提交仍由司机在表单/确认卡
点击后走 /api/entry/applications 系列接口完成。

工具之间共享的结构化状态放在请求级 contextvar，而不是让 LLM 反复搬运
一整包身份证号/手机号：模型只回传「本轮新抽到的字段」，工具负责合并与清洗。
generate_node 从 ctx.get_payload() 取回带 __entry_payload__ 标记的结果，
再通过 SSE done / ChatResponse 交给前端渲染确认卡。
"""

import json
import logging
from typing import Any, Dict

from ..core import entry_context as ctx
from ..services import entry_schema as schema
from ..services import entry_service as svc

logger = logging.getLogger(__name__)

PAYLOAD_KEY = "__entry_payload__"


def _current_slots() -> Dict[str, Any]:
    """优先用本轮工具已合并的 slots，否则用前端回传的初始 slots。"""
    p = ctx.get_payload()
    return dict(p.get("slots") or ctx.slots() or {})


def _emit(slots: Dict[str, Any], note: str = "") -> str:
    payload = schema.build_entry_payload(slots, note=note)
    ctx.set_payload(payload)
    return schema.to_json(payload)


def _identity_guard() -> str:
    if not ctx.username():
        return schema.to_json({"error": "无法确定当前用户身份，请重新登录"})
    return ""


# ── 工具 1：解析材料 ──

async def tool_parse_entry_materials(slots: Dict[str, Any] = None,
                                     user_input: str = "") -> str:
    """把模型从司机原文抽到的字段，与已有字段合并、清洗、校验。

    slots 只放「本轮新抽到的字段」；未提到的字段保持原值。
    """
    guard = _identity_guard()
    if guard:
        return guard
    base = _current_slots()
    incoming = schema.sanitize_slots(slots or {})

    # 高置信字段（手机号、身份证、车牌、时间、重量）用正则兜底补漏，
    # 但绝不覆盖模型已给出的值，也绝不编造文本类字段。
    # 例外：模型给出的高置信值若根本没在原文里独立出现（例如把身份证号
    # 中间的 11 位当手机号），先用规则抽取值校正，避免假号码进申请单。
    raw = user_input or ctx.user_input()
    rule_based = schema.extract_by_rules(raw)
    incoming = schema.reconcile_high_confidence(incoming, rule_based, raw)
    for k, v in rule_based.items():
        incoming.setdefault(k, v)

    merged = schema.merge_slots(base, incoming)
    return _emit(merged, note="已合并本轮材料")


# ── 工具 2：取本人已上传附件（显式 ID，不自动取「每类最新一张」）──

async def tool_get_my_entry_attachments() -> str:
    guard = _identity_guard()
    if guard:
        return guard
    rows = await svc.list_my_attachments(ctx.username(), hours=48)
    slots = _current_slots()
    atts = dict(slots.get("attachments") or {})

    # 仅当某类缺失且「未被任何申请单占用」的该类附件恰好只有一张时才回填，
    # 多张时把清单交给 Agent 让司机明确选，杜绝 A 单证件被 B 单复用。
    unbound: Dict[str, list] = {}
    for r in rows:
        if not r.get("bound"):
            unbound.setdefault(r["kind"], []).append(r)
    filled = []
    for kind, label in schema.ATTACHMENT_KINDS.items():
        if atts.get(kind):
            continue
        candidates = unbound.get(kind) or []
        if len(candidates) == 1:
            atts[kind] = candidates[0]["id"]
            filled.append(label)
    if atts:
        slots = dict(slots)
        slots["attachments"] = atts

    payload_note = ("已自动关联：" + "、".join(filled)) if filled else "未自动关联任何附件"
    out = _emit(slots, note=payload_note)
    # 附加附件清单，方便 Agent 回答「我传了哪些照片」
    data = json.loads(out)
    data["attachments"] = rows
    data["hint"] = (
        (data.get("hint") or "") +
        " 附件清单已列出，请让司机核对确认卡上的照片/证件是否正确；"
        "未匹配的类别需要司机重新上传或在确认卡里手动选择。"
    )
    ctx.set_payload(data)
    return schema.to_json(data)


# ── 工具 3：确定性规则校验 ──

async def tool_check_entry_rules() -> str:
    guard = _identity_guard()
    if guard:
        return guard
    slots = _current_slots()
    check = schema.validate(slots, for_submit=False)
    data = schema.build_entry_payload(slots, note="规则校验完成")
    data["rule_check"] = check
    ctx.set_payload(data)
    return schema.to_json(data)


# ── 工具 4：推确认卡 ──

async def tool_push_entry_confirm_card() -> str:
    """必填齐备才出卡；这是唯一能把 card 交给前端的工具。"""
    guard = _identity_guard()
    if guard:
        return guard
    slots = _current_slots()
    data = schema.build_entry_payload(slots, note="收口判断")
    if not data["complete"]:
        data["card"] = None
        data["error"] = "信息未齐备，不能生成确认卡：" + "、".join(data["missing"][:6])
    ctx.set_payload(data)
    return schema.to_json(data)


ENTRY_TOOL_REGISTRY = {
    "parse_entry_materials": tool_parse_entry_materials,
    "get_my_entry_attachments": tool_get_my_entry_attachments,
    "check_entry_rules": tool_check_entry_rules,
    "push_entry_confirm_card": tool_push_entry_confirm_card,
}

_SLOT_PROPERTIES = schema.llm_slots_schema()["properties"]

ENTRY_TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "parse_entry_materials",
            "description": (
                "解析司机这段话里包含的入园申请信息，与已有字段合并后返回结构化结果。"
                "只要司机提供了任何新信息就应调用；只传本轮新抽到的字段，"
                "不要重复传已有字段，禁止编造、猜测或补全任何未提供的值。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "slots": {
                        "type": "object",
                        "description": "本轮新抽到的字段，键必须是下列合法字段之一",
                        "properties": _SLOT_PROPERTIES,
                        "additionalProperties": False,
                    },
                    "user_input": {
                        "type": "string",
                        "description": "司机本轮原话（可选，用于正则兜底提取手机号/证件号/车牌/时间）",
                    },
                },
                "required": ["slots"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_my_entry_attachments",
            "description": (
                "查询当前登录账号已上传、且未被其它申请单占用的车辆照片与证件清单。"
                "司机提到照片/证件已上传、或需要补齐五类附件时调用。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_entry_rules",
            "description": (
                "对当前已收集字段做确定性校验（必填项、重量上限、入园/出园时间先后）。"
                "回答「还缺什么」或准备收口前必须调用，不要自己判断字段是否齐备。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "push_entry_confirm_card",
            "description": (
                "字段与五类附件齐备后生成信息核对卡交给司机。"
                "仅当 check_entry_rules 返回 complete=true 时可调用；"
                "AI 不会代司机提交申请，也不会写入数据库。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

ENTRY_AGENT_SYSTEM_PROMPT = """你是园区「司机入园申请」助手，只负责收集与核对信息，不办理审批、不提交申请、不写入数据库。

工作规则：
1. 司机给一段话或照片时，先调用 parse_entry_materials 把新信息合并进已有字段。
2. 需要照片/证件时调用 get_my_entry_attachments；五类附件为：车头照片、车尾照片、行驶证、驾驶证、危化品运输证。
3. 回答「还缺什么」、准备收口前，必须调用 check_entry_rules，不要凭自己的感觉判断字段是否齐备。
4. 只有 check_entry_rules 返回 complete=true，才调用 push_entry_confirm_card 推核对卡。
5. 缺项时用自然的口语一次性问清（最多 4 项），不要逐条追问十几轮，不要编造任何字段值。
6. 身份证号、手机号等敏感信息不要在回复里完整复述，确认卡会自动打码。
7. 明确告诉司机：核对无误后由他本人在确认卡或申请表单里点击提交，AI 不会代替提交。

可收集字段：
""" + schema.describe_fields_for_prompt()
