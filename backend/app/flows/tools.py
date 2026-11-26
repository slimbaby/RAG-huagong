"""
通用只读四件套（docs/10 §5.1）—— 全平台只有一份工具名，参数 schema 按 flow_id 派生。

parse_flow_materials / get_my_attachments / check_flow_rules / push_flow_confirm_card
四个工具全部只读、无 submit；工具集按 flow_id 裁剪，普通知识库问答看不到任何流程工具。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from . import context as ctx
from . import hooks as hook_registry
from . import service as svc
from .model import FlowDefinition, to_json

logger = logging.getLogger(__name__)

PAYLOAD_KEY = "__flow_payload__"


def _definition() -> FlowDefinition:
    d = ctx.definition()
    if d is None:
        raise RuntimeError("未绑定流程定义，无法调用流程工具")
    return d


def _guard() -> str:
    if not ctx.username():
        return to_json({"error": "无法确定当前用户身份，请重新登录"})
    if ctx.definition() is None:
        return to_json({"error": "当前请求未绑定已发布流程配置"})
    return ""


def _current_slots() -> Dict[str, Any]:
    p = ctx.get_payload()
    return dict(p.get("slots") or ctx.slots() or {})


def _emit(definition: FlowDefinition, slots: Dict[str, Any], note: str = "") -> str:
    payload = definition.build_payload(slots, note=note)
    ctx.set_payload(payload)
    return to_json(payload)


# ── 工具 1：解析材料 ──
async def tool_parse_flow_materials(slots: Dict[str, Any] = None,
                                    user_input: str = "") -> str:
    """先跑 fields[].extract 确定性抽取，再合并模型补缺；禁止编造。"""
    guard = _guard()
    if guard:
        return guard
    d = _definition()
    base = _current_slots()
    incoming = d.sanitize_slots(slots or {})
    raw = user_input or ctx.user_input()
    rule_based = d.extract_by_rules(raw)
    # after_parse hook（reconcile_high_confidence）统一在这里消费
    for ref in d.hook_refs("after_parse"):
        hook = hook_registry.get_hook(str(ref.get("name")))
        if hook is None:
            continue
        try:
            result = hook["fn"](d, {"incoming": incoming, "rule_based": rule_based,
                                    "text": raw}, ref.get("params") or {}) or {}
            if isinstance(result.get("slots"), dict):
                incoming = d.sanitize_slots(result["slots"])
        except Exception as e:
            logger.warning(f"[flow_tools] after_parse hook {ref.get('name')} 失败: {e}")
    for k, v in rule_based.items():
        incoming.setdefault(k, v)
    merged = d.merge_slots(base, incoming)
    return _emit(d, merged, note="已合并本轮材料")


# ── 工具 2：本人已上传附件（显式 ID）──
async def tool_get_my_attachments() -> str:
    guard = _guard()
    if guard:
        return guard
    d = _definition()
    rows = await svc.list_my_attachments(d, ctx.username(), hours=d.ttl_hours)
    slots = _current_slots()
    atts = dict(slots.get("attachments") or {})
    unbound: Dict[str, list] = {}
    for r in rows:
        if not r.get("bound"):
            unbound.setdefault(r["kind"], []).append(r)
    filled = []
    for kind, label in d.attachment_kinds.items():
        if atts.get(kind):
            continue
        candidates = unbound.get(kind) or []
        if len(candidates) == 1:
            atts[kind] = candidates[0]["id"]
            filled.append(label)
    if atts:
        slots = dict(slots)
        slots["attachments"] = atts
    out = _emit(d, slots, note=("已自动关联：" + "、".join(filled)) if filled else "未自动关联任何附件")
    data = json.loads(out)
    data["attachments"] = rows
    data["hint"] = (data.get("hint") or "") + " 附件清单已列出，请让用户核对确认卡；未匹配类别需重新上传或手动选择。"
    ctx.set_payload(data)
    return to_json(data)


# ── 工具 3：确定性规则校验（不查知识库）──
async def tool_check_flow_rules() -> str:
    guard = _guard()
    if guard:
        return guard
    d = _definition()
    slots = _current_slots()
    check = d.validate(slots, for_submit=False)
    data = d.build_payload(slots, note="规则校验完成")
    data["rule_check"] = check
    ctx.set_payload(data)
    return to_json(data)


# ── 工具 4：推确认卡 ──
async def tool_push_flow_confirm_card() -> str:
    guard = _guard()
    if guard:
        return guard
    d = _definition()
    slots = _current_slots()
    data = d.build_payload(slots, note="收口判断")
    if not data["complete"]:
        data["card"] = None
        data["error"] = "信息未齐备，不能生成确认卡：" + "、".join(data["missing"][:6])
    ctx.set_payload(data)
    return to_json(data)


FLOW_TOOL_REGISTRY = {
    "parse_flow_materials": tool_parse_flow_materials,
    "get_my_attachments": tool_get_my_attachments,
    "check_flow_rules": tool_check_flow_rules,
    "push_flow_confirm_card": tool_push_flow_confirm_card,
}


def flow_tools_schema(definition: FlowDefinition) -> List[Dict[str, Any]]:
    """参数 properties 完全由 fields[] 派生；流程不同，只变 schema，工具名不变。"""
    props = definition.llm_slots_schema()["properties"]
    kinds = "、".join(definition.attachment_kinds.values()) or "（该流程无需附件）"
    return [
        {
            "type": "function",
            "function": {
                "name": "parse_flow_materials",
                "description": (
                    f"解析用户这段话里包含的「{definition.name}」信息，与已有字段合并后返回结构化结果。"
                    "只要用户提供了任何新信息就应调用；只传本轮新抽到的字段，"
                    "禁止编造、猜测或补全任何未提供的值。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "slots": {
                            "type": "object",
                            "description": "本轮新抽到的字段，键必须是下列合法字段之一",
                            "properties": props,
                            "additionalProperties": False,
                        },
                        "user_input": {
                            "type": "string",
                            "description": "用户本轮原话（用于按配置做确定性抽取）",
                        },
                    },
                    "required": ["slots"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_my_attachments",
                "description": (
                    "查询当前登录账号已上传、按流程与复用策略过滤、且未被占用的附件清单。"
                    f"必交附件类别：{kinds}。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "check_flow_rules",
                "description": (
                    "对当前已收集字段做确定性校验（必填、格式、范围、业务规则）。"
                    "回答「还缺什么」或准备收口前必须调用，不要自己判断是否齐备。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "push_flow_confirm_card",
                "description": (
                    "字段与必交附件齐备后生成信息核对卡交给用户。"
                    "仅当 check_flow_rules 返回 complete=true 时可调用；"
                    "AI 不会代替用户提交，也不会写入数据库。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]


def flow_system_prompt(definition: FlowDefinition) -> str:
    return definition.system_prompt()
