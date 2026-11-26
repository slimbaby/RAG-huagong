"""
最小可用流程模板（业务同事在后台新建流程时的起点）。

模板本身是一份能直接通过 dry-run 八项校验的完整元模型配置：
字段、意图规则、审批链、入口、权限都已齐备，业务只需改文字与字段。
开发者不需要为新流程写任何代码。
"""
from __future__ import annotations

import re
from typing import Any, Dict

from .model import FRAMEWORK_TRANSITIONS


def build_minimal_definition(flow_id: str, name: str, *, keyword: str = "") -> Dict[str, Any]:
    """生成一份最小但可发布的流程配置。

    keyword 用作该流程的意图场景词，必须与其他已发布流程区分开，
    否则 dry-run 的「跨流程正则冲突」会拦截发布。
    """
    fid = str(flow_id or "").strip()
    label = str(name or fid or "新流程").strip()
    word = str(keyword or label).strip()
    escaped = re.escape(word)
    default_roles = ["general_viewer", "driver", "finance_user", "warehouse_user", "qc_user"]
    fields = [
        {"key": "item_name", "label": "事项名称", "group": "base", "type": "string",
         "required": True, "order": 0, "source": "user", "placeholder": "如：危化品入库"},
        {"key": "apply_reason", "label": "申请事由", "group": "base", "type": "string",
         "required": True, "order": 1, "source": "user", "placeholder": "简要说明办理原因"},
        {"key": "plan_date", "label": "计划日期", "group": "base", "type": "date",
         "required": False, "order": 2, "source": "user", "placeholder": "2026-12-01"},
    ]
    return {
        "meta": {"flow_id": fid, "name": label,
                 "description": f"{label}：由后台配置生成，确认后写库、admin 审核",
                 "version": 1, "status": "draft", "owner": "流程配置后台"},
        "groups": [{"key": "base", "label": "基本信息"}],
        "fields": fields,
        "intent": {
            "rule_pipeline": [
                {"id": "p0_head_request", "phase": "p0_head_request",
                 "when": "head_apply_verb_and_domain",
                 "patterns": [
                     r"^\s*(?:师傅|你好|您好|嗨|嗯|那个)?[\s，,]*(?:帮我|我要|我想|给我|麻烦|请帮我|能帮我|可以帮我|来).{0,8}(?:申请|办理|提交|预约|报备|登记|办一下|办)",
                     escaped,
                 ],
                 "outcome": "action", "stop_on_match": True},
                {"id": "p1_readonly", "phase": "p1_readonly", "when": "readonly",
                 "patterns": [f"^(?:查|查询|咨询|请问|了解一下).{{0,16}}{escaped}"],
                 "outcome": "readonly", "stop_on_match": True},
                {"id": "p2_action", "phase": "p2_general", "when": "apply_verb_and_domain",
                 "patterns": [f"(?:申请|办理|提交|登记).{{0,12}}{escaped}"],
                 "outcome": "action", "stop_on_match": True},
            ],
            "domain_words": [word, "补材料"],
            "ambiguous_hint": f"您是要办理「{label}」，还是只想咨询相关规定？",
            "tie_breaker": 20,
            "classifier": {"mode": "rules", "fallback": "rules"},
            "test_cases": [
                {"text": f"帮我办理{word}", "expect_flow_id": fid, "expect_action": "action", "group": "办理"},
                {"text": f"咨询一下{word}的办理说明", "expect_flow_id": fid, "expect_action": "readonly", "group": "只读"},
                {"text": f"能帮我办理{word}吗？", "expect_flow_id": fid, "expect_action": "choice",
                 "expect_choice": True, "group": "两可"},
            ],
        },
        "attachments": [
            {"key": "support_file", "label": "支撑材料", "required": False, "max_count": 1,
             "accept": ".jpg,.jpeg,.png,.webp,.pdf", "max_size_mb": 10,
             "reuse_policy": "per_application"},
        ],
        "workflow": {
            "state_labels": {"draft": "草稿（待确认提交）", "pending": "待审核",
                             "approved": "审核通过", "rejected": "已驳回", "cancelled": "已撤回"},
            "allowed_transitions": [
                {"from": f, "to": t, "action": a,
                 "actor_role": "admin" if a == "review" else "driver"}
                for f, t, a in FRAMEWORK_TRANSITIONS
            ],
            "pass": {"enabled": False},
        },
        "session": {"session_prefix": f"{fid}-", "keep_flow_on_followup": True,
                    "switch_policy": "confirm", "choice_loop_guard": "intent_resolved_once",
                    "ttl_hours": 2, "version_pin": True},
        "entry_points": [
            {"type": "menu", "key": f"{fid}_apply", "label": f"{label}申请",
             "path": f"/flows/{fid}/apply", "visible_roles": default_roles + ["admin"],
             "icon": "FileText", "order": 20},
            {"type": "menu", "key": f"{fid}_my", "label": f"我的{label}",
             "path": f"/flows/{fid}/my", "visible_roles": default_roles + ["admin"],
             "icon": "ClipboardList", "order": 21},
            {"type": "menu", "key": f"{fid}_review", "label": f"{label}审核",
             "path": f"/flows/{fid}/review", "visible_roles": ["admin"],
             "icon": "ShieldCheck", "order": 22},
            {"type": "shortcut", "key": f"{fid}_ai", "label": f"AI {label}",
             "visible_roles": default_roles + ["admin"], "icon": "Sparkles", "order": 2},
        ],
        "card": {"title": f"{label}信息核对", "field_keys": [f["key"] for f in fields],
                 "prefill_target": f"/flows/{fid}/apply"},
        "prompts": {
            "system_prompt": (
                f"你是园区「{label}」助手，只负责收集与核对信息，不办理审批、不提交申请、不写入数据库。\n"
                "1. 用户提供新信息时先调用 parse_flow_materials 合并字段。\n"
                "2. 需要材料时调用 get_my_attachments。\n"
                "3. 收口前必须调用 check_flow_rules；只有 complete=true 才能调用 push_flow_confirm_card。\n"
                "4. 缺项时一次性问清，不要编造字段值；确认后由用户本人在表单里提交。"
            ),
            "ask_hints": [], "examples": [],
        },
        "hooks": [],
        "permissions": {"applicant_roles": default_roles, "reviewer_roles": ["admin"],
                        "scope": fid},
    }
