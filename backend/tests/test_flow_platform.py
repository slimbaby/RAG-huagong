"""多流程配置化平台回归（纯函数层，不依赖 DB / 网络）。

覆盖：元模型 dry-run 八项、审批链硬约束、hook 白名单、后端权威意图路由、
配置模板（第二流程零改动）、PII 打码与渲染元数据派生。
"""
import copy
import json
import os

import pytest

from app.flows.model import FlowConfigError, FlowDefinition, compile_safe_regex
from app.flows.registry import classify_text
from app.flows.seed import build_driver_entry_definition
from app.flows.template import build_minimal_definition
from app.flows.validator import validate_definition

CASES_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "shared", "entry_intent_cases.json")
)


def _driver() -> FlowDefinition:
    return FlowDefinition(build_driver_entry_definition())


def _new_flow() -> FlowDefinition:
    return FlowDefinition(build_minimal_definition("warehouse_inbound", "危化品入库申请"))


# ── 1. 元模型与 dry-run ──

def test_driver_entry_definition_passes_all_eight_checks():
    d = _driver()
    assert len(d.fields) == 13
    assert len(d.attachments) == 5
    res = validate_definition(d)
    assert res["ok"], res["errors"]
    assert len(res["checks"]) == 8
    assert all(c["ok"] for c in res["checks"])
    assert res["test_cases"]["total"] == 40
    assert res["test_cases"]["passed"] == 40, res["test_cases"]["failed"]


def test_shared_baseline_cases_migrated_into_db_config():
    with open(CASES_PATH, encoding="utf-8") as f:
        baseline = json.load(f)["cases"]
    cases = _driver().test_cases
    assert len(cases) >= len(baseline) >= 40
    texts = {c["text"] for c in cases}
    assert all(c["q"] in texts for c in baseline)


def test_render_config_has_no_pii_values_and_dynamic_paths():
    cfg = _driver().render_config()
    assert cfg["flow_id"] == "driver_entry"
    assert {f["key"] for f in cfg["fields"]} == set(_driver().field_keys)
    assert cfg["card"]["prefill_target"] == "/flows/driver_entry/apply"
    allowed = {"key", "label", "group", "type", "required", "options",
               "placeholder", "pii", "visible_when", "order"}
    # 只下发表单元数据，不下发任何字段值 / 证件号 / 手机号
    assert all(set(f) <= allowed for f in cfg["fields"])


def test_mask_and_card_never_leak_pii():
    d = _driver()
    slots = {"phone": "13800138000", "id_no": "370102199001011234", "plate_no": "鲁A12345"}
    card = d.build_card(slots)
    flat = json.dumps(card, ensure_ascii=False)
    assert "13800138000" not in flat
    assert "370102199001011234" not in flat
    assert "鲁A12345" in flat


# ── 2. 审批链硬约束（v3 D7）──

@pytest.mark.parametrize("mutate,expect", [
    ("drop_reviews", "缺少人工审批流转"),
    ("direct_approve", "禁止 draft→approved 直连"),
    ("drop_submit", "缺少 draft→pending 的 submit 流转"),
    ("reviewer_not_admin", "actor_role 必须"),
])
def test_validator_rejects_broken_approval_chain(mutate, expect):
    raw = copy.deepcopy(build_driver_entry_definition())
    transitions = raw["workflow"]["allowed_transitions"]
    if mutate == "drop_reviews":
        transitions[:] = [t for t in transitions if t["action"] != "review"]
    elif mutate == "direct_approve":
        transitions.append({"from": "draft", "to": "approved", "action": "submit",
                            "actor_role": "driver"})
    elif mutate == "drop_submit":
        transitions[:] = [t for t in transitions if t["action"] != "submit"]
    elif mutate == "reviewer_not_admin":
        for t in transitions:
            if t["action"] == "review":
                t["actor_role"] = "driver"
    res = validate_definition(FlowDefinition(raw))
    assert not res["ok"]
    assert any(expect in e for e in res["errors"]), res["errors"]


# ── 3. hook 白名单与正则安全 ──

def test_unknown_hook_and_bad_phase_are_rejected():
    raw = copy.deepcopy(build_driver_entry_definition())
    raw["hooks"].append({"name": "eval_arbitrary_code", "phase": "before_submit", "params": {}})
    assert any("未注册的扩展点" in e for e in validate_definition(FlowDefinition(raw))["errors"])

    raw = copy.deepcopy(build_driver_entry_definition())
    raw["hooks"][0]["phase"] = "on_review"
    assert any("phase" in e for e in validate_definition(FlowDefinition(raw))["errors"])


def test_forbidden_hook_params_are_rejected():
    from app.flows import hooks as hook_registry
    for bad in ("${env}", "__import__('os')", "lambda x: x", "eval(1)", "os.system('ls')"):
        errors = hook_registry.validate_params("validate_weight_limit", {"field": bad})
        assert any("表达式 / 脚本" in e or "不接受参数" in e for e in errors), bad


def test_safe_regex_allows_non_capturing_groups_and_blocks_redos():
    assert compile_safe_regex(r"(?:帮我|我要).{0,8}(?:申请|办理)")
    assert compile_safe_regex(r"(?<!不)申请入园")
    with pytest.raises(FlowConfigError):
        compile_safe_regex(r"(a+)+$")
    with pytest.raises(FlowConfigError):
        compile_safe_regex(r"a" * 500)


# ── 4. 后端权威意图路由（D10）──

def test_ambiguous_question_returns_choice_card():
    d = _driver()
    decision = classify_text("能帮我申请入园吗？", [d])
    assert decision["needs_choice"] is True
    assert decision["action"] == "choice"
    assert decision["candidates"][0]["flow_id"] == "driver_entry"


def test_action_and_readonly_short_circuit():
    d = _driver()
    assert classify_text("帮我申请入园，我叫张三", [d])["action"] == "action"
    assert classify_text("入园要带什么材料", [d])["action"] == "readonly"
    assert classify_text("甲醇的闪点是多少", [d])["action"] == "none"


def test_intent_resolved_and_forced_flow():
    d = _driver()
    out = classify_text("能帮我申请入园吗？", [d], forced_flow_id="driver_entry",
                        forced_action="action", intent_resolved=True)
    assert out["flow_id"] == "driver_entry"
    assert out["action"] == "action"
    assert out["needs_choice"] is False


def test_chitchat_does_not_stick_to_flow_session():
    d = _driver()
    for text in ("谢谢", "今天天气真好啊", "讲个笑话", "周末愉快"):
        out = classify_text(text, [d], session_flow_id="driver_entry")
        assert out["flow_id"] is None, (text, out)


def test_session_keep_for_followup_material():
    d = _driver()
    out = classify_text("我的手机号是13800138000", [d], session_flow_id="driver_entry")
    assert out["flow_id"] == "driver_entry"
    assert out["rule_id"] == "session_keep"


def test_submitted_session_does_not_hijack_followup_questions():
    """提交后会话已办结：闲聊/知识问答回到普通 RAG，不再续留办理通道重出核对卡。"""
    d = _driver()
    for text in ("今天天气真好啊", "我还能帮别人申请吗", "甲醇的闪点是多少", "我要入院"):
        out = classify_text(text, [d], session_flow_id="driver_entry", session_completed=True)
        # none 或 readonly 都不会挂办理工具，也就不会重出核对卡
        assert out["action"] not in ("action", "attach", "choice"), (text, out)
        assert out["rule_id"] != "session_keep", (text, out)


def test_explicit_apply_still_works_after_submission():
    """已办结后用户明确要再办一张：P0/P2 规则命中，不受 session_completed 影响。"""
    d = _driver()
    out = classify_text("帮我申请入园，我叫张三", [d],
                        session_flow_id="driver_entry", session_completed=True)
    assert out["flow_id"] == "driver_entry"
    assert out["action"] == "action"
    assert out["rule_id"] != "session_completed"


def test_in_progress_session_keeps_material_followup():
    """未提交（draft/rejected）时会话兜底仍生效，补材料不会被打断。"""
    d = _driver()
    out = classify_text("我的手机号是13800138000", [d],
                        session_flow_id="driver_entry", session_completed=False)
    assert out["flow_id"] == "driver_entry"
    assert out["rule_id"] == "session_keep"


def test_two_flows_with_same_priority_produce_candidate_card():
    raw1 = build_minimal_definition("flow_a", "流程甲", keyword="甲业务")
    raw2 = build_minimal_definition("flow_b", "流程乙", keyword="乙业务")
    for raw in (raw1, raw2):
        raw["intent"]["rule_pipeline"][2]["patterns"] = [r"帮我申请"]
    out = classify_text("帮我申请", [FlowDefinition(raw1), FlowDefinition(raw2)])
    assert out["action"] == "choice"
    assert len(out["candidates"]) == 2


# ── 5. 第二流程零改动：模板直接通过 dry-run ──

def test_minimal_template_passes_dry_run_with_peer_flows():
    d = _new_flow()
    res = validate_definition(d, others=[_driver()])
    assert res["ok"], res["errors"]
    assert res["test_cases"]["passed"] == 3, res["test_cases"]["failed"]


def test_minimal_template_has_full_approval_chain_and_dynamic_menu():
    d = _new_flow()
    assert d.find_transition("submit", "draft", "pending") is not None
    assert d.find_transition("review", "pending", "approved") is not None
    paths = [ep["path"] for ep in d.entry_points if ep.get("path")]
    assert all(p.startswith("/flows/warehouse_inbound/") for p in paths)
    assert d.render_config()["card"]["prefill_target"] == "/flows/warehouse_inbound/apply"


def test_template_intent_routing_hits_only_its_own_flow():
    peer = _driver()
    d = _new_flow()
    out = classify_text("帮我办理危化品入库申请", [peer, d])
    assert out["flow_id"] == "warehouse_inbound"
    assert out["action"] == "action"


def test_config_roles_cannot_appear_in_business_permissions():
    raw = copy.deepcopy(build_driver_entry_definition())
    raw["permissions"]["applicant_roles"] = ["config_editor"]
    res = validate_definition(FlowDefinition(raw))
    assert any("不能出现在 applicant_roles" in e for e in res["errors"])
