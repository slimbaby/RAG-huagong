"""
dry-run 八项校验（docs/10 §6）—— 配置没有编译期检查，这里是发布前的唯一闸门。

校验失败一律拒绝发布；警告只提示，不阻断。全部结果写 flow_audit_log。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import hooks as hook_registry
from .model import (
    ADMIN_LEVEL_ROLES, CONFIG_ROLES, FIELD_TYPES, FRAMEWORK_STATES, FRAMEWORK_TRANSITIONS,
    HOOK_PHASES, INTENT_OUTCOMES, INTENT_PHASES, REUSE_POLICIES, ROLE_WHITELIST,
    SECTION_KEYS, FlowConfigError, FlowDefinition, compile_safe_regex, parse_dt,
)

_FLOW_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_PATH_RE = re.compile(r"^/flows/[a-z][a-z0-9_]{1,63}/(apply|my|review|detail)(?:/.*)?$")


def _check(cid: str, name: str, errors: List[str]) -> Dict[str, Any]:
    return {"id": cid, "name": name, "ok": not errors, "details": list(errors)}


def validate_definition(definition: Dict[str, Any] | FlowDefinition, *,
                        others: Optional[List[FlowDefinition]] = None,
                        run_cases: bool = True) -> Dict[str, Any]:
    """返回 {ok, errors, warnings, checks, test_cases}。"""
    try:
        d = definition if isinstance(definition, FlowDefinition) else FlowDefinition(definition)
    except FlowConfigError as e:
        return {"ok": False, "errors": [str(e)], "warnings": [], "checks": [], "test_cases": {}}
    peer = [p for p in (others or []) if p.flow_id != d.flow_id]
    errors: List[str] = []
    warnings: List[str] = []
    checks: List[Dict[str, Any]] = []

    # ── 1 元模型自校验 ──
    e1: List[str] = []
    raw = d.raw
    for key in SECTION_KEYS:
        if key not in raw:
            e1.append(f"缺少配置段 {key}")
    fid = d.flow_id
    if not _FLOW_ID_RE.match(fid):
        e1.append("meta.flow_id 必须匹配 ^[a-z][a-z0-9_]{1,63}$")
    if not str(d.meta.get("name") or "").strip():
        e1.append("meta.name 不能为空")
    if not d.fields:
        e1.append("fields 至少要有一个字段")
    keys = [str(f.get("key") or "") for f in d.fields]
    if len(keys) != len(set(keys)):
        e1.append("fields[].key 必须唯一")
    for f in d.fields:
        key = str(f.get("key") or "")
        if not re.match(r"^[a-z][a-z0-9_]{0,63}$", key):
            e1.append(f"字段 key 非法: {key}")
        if str(f.get("type") or "string") not in FIELD_TYPES:
            e1.append(f"{key} 的 type 不合法")
        if f.get("type") == "enum" and not f.get("options"):
            e1.append(f"{key} 是 enum 但缺 options")
        if f.get("visible_when"):
            cond = f["visible_when"]
            if not isinstance(cond, dict) or not cond.get("field"):
                e1.append(f"{key}.visible_when 必须指定 field")
            elif str(cond["field"]) not in keys:
                e1.append(f"{key}.visible_when 引用了不存在的字段 {cond['field']}")
        validation = f.get("validation") or {}
        if validation.get("regex"):
            try:
                compile_safe_regex(str(validation["regex"]))
            except FlowConfigError as ex:
                e1.append(f"{key}.validation.regex: {ex}")
    for a in d.attachments:
        if str(a.get("reuse_policy") or "per_application") not in REUSE_POLICIES:
            e1.append(f"附件 {a.get('key')} 的 reuse_policy 不合法")
    # 状态 key 必须属于框架保留枚举（D9）
    labels = d.workflow.get("state_labels") or {}
    for k in labels:
        if k not in FRAMEWORK_STATES:
            e1.append(f"state_labels 含非框架状态 key: {k}")
    for t in d.transitions:
        pair = (str(t.get("from")), str(t.get("to")), str(t.get("action")))
        if pair not in FRAMEWORK_TRANSITIONS:
            e1.append(f"流转 {pair[0]}→{pair[1]}({pair[2]}) 不在框架允许的流转集合内")
    if not d.intent.get("rule_pipeline"):
        e1.append("intent.rule_pipeline 不能为空")
    for ep in d.entry_points:
        if str(ep.get("type")) not in ("menu", "shortcut"):
            e1.append(f"入口 {ep.get('key')} 的 type 只能是 menu / shortcut")
    checks.append(_check("1", "元模型自校验", e1))
    errors.extend(e1)

    # ── 2 引用一致性 ──
    e2: List[str] = []
    for key in d.card.get("field_keys") or []:
        if str(key) not in d.field_keys:
            e2.append(f"card.field_keys 引用了不存在的字段 {key}")
    for t in d.transitions:
        guard = t.get("guard_hook")
        if guard and hook_registry.get_hook(str(guard)) is None:
            e2.append(f"流转守卫 hook {guard} 未注册")
    for f in d.fields:
        ext = f.get("extract") or {}
        for target in ext.get("targets") or []:
            if str(target) not in d.field_keys:
                e2.append(f"{f['key']}.extract.targets 引用了不存在的字段 {target}")
        ref = ext.get("from_attachment")
        if ref and str(ref) not in d.attachment_kinds:
            e2.append(f"{f['key']}.extract.from_attachment 引用了不存在的附件 {ref}")
    if d.workflow.get("pass", {}).get("hook"):
        if hook_registry.get_hook(str(d.workflow["pass"]["hook"])) is None:
            e2.append("workflow.pass.hook 未注册")
    checks.append(_check("2", "引用一致性", e2))
    errors.extend(e2)

    # ── 3 hook 白名单 / phase / params 值域 / 生命周期 ──
    e3: List[str] = []
    for ref in d.hooks:
        name = str(ref.get("name") or "")
        hook = hook_registry.get_hook(name)
        if hook is None:
            e3.append(f"未注册的扩展点 {name}")
            continue
        if str(ref.get("phase")) != hook["phase"]:
            e3.append(f"{name} 的 phase 应为 {hook['phase']}，配置写了 {ref.get('phase')}")
        if str(ref.get("phase")) not in HOOK_PHASES:
            e3.append(f"{name} 的 phase 不合法")
        e3.extend(hook_registry.validate_params(name, ref.get("params") or {}))
        try:
            hook_registry.assert_can_reference(name)
        except FlowConfigError as ex:
            e3.append(str(ex))
    checks.append(_check("3", "扩展点白名单与参数值域", e3))
    errors.extend(e3)

    # ── 4 意图流水线 / 正则 / 跨流程冲突 ──
    e4: List[str] = []
    pipeline = d.intent.get("rule_pipeline") or []
    phase_order = {p: i for i, p in enumerate(INTENT_PHASES)}
    last_order, ids = -1, set()
    for i, row in enumerate(pipeline):
        rid = str(row.get("id") or "")
        if not rid or rid in ids:
            e4.append(f"rule_pipeline 第 {i+1} 行 id 缺失或重复")
        ids.add(rid)
        phase = str(row.get("phase") or "")
        if phase not in phase_order:
            e4.append(f"规则 {rid} 的 phase 不合法: {phase}")
        elif phase_order[phase] < last_order:
            e4.append(f"规则 {rid} 的 phase 顺序倒退（必须 P0→P1→P2）")
        else:
            last_order = phase_order[phase]
        if str(row.get("outcome")) not in INTENT_OUTCOMES:
            e4.append(f"规则 {rid} 的 outcome 不合法")
        for p in row.get("patterns") or []:
            try:
                compile_safe_regex(str(p))
            except FlowConfigError as ex:
                e4.append(f"规则 {rid} 的正则: {ex}")
        if str(row.get("when")) not in (
            "head_apply_verb_and_domain", "any_pattern", "apply_verb_and_domain",
            "readonly", "domain_words", "never",
        ):
            e4.append(f"规则 {rid} 的 when 不合法")
    classifier = d.classifier
    if classifier.get("mode") not in ("rules", "hybrid"):
        e4.append("intent.classifier.mode 只能是 rules / hybrid")
    if classifier.get("mode") == "hybrid":
        warnings.append("本版不接外部判定模型，hybrid 将自动降级为 rules")
    # 冲突检测：字面量 pattern 被多个已发布流程同时使用
    mine = {str(p) for row in pipeline for p in (row.get("patterns") or [])}
    for other in peer:
        theirs = {str(p) for row in (other.intent.get("rule_pipeline") or [])
                  for p in (row.get("patterns") or [])}
        overlap = mine & theirs
        if overlap:
            e4.append(f"与流程 {other.flow_id} 的意图正则重复: {sorted(overlap)[:3]}")
        domain_overlap = set(map(str, d.intent.get("domain_words") or [])) & set(
            map(str, other.intent.get("domain_words") or []))
        if domain_overlap and len(domain_overlap) >= 2:
            warnings.append(f"与流程 {other.flow_id} 的场景词高度重叠: {sorted(domain_overlap)[:4]}")
    checks.append(_check("4", "意图流水线与冲突检测", e4))
    errors.extend(e4)

    # ── 5 用例回归（用例在 DB，后端跑，D10/矛盾 4）──
    case_result: Dict[str, Any] = {"total": len(d.test_cases), "passed": 0, "failed": []}
    e5: List[str] = []
    if run_cases and d.test_cases:
        from .registry import classify_text
        definitions = [d] + peer
        for idx, case in enumerate(d.test_cases):
            text = str(case.get("text") or "")
            decision = classify_text(text, definitions)
            expect_flow = str(case.get("expect_flow_id") or fid)
            expect_action = str(case.get("expect_action") or "")
            expect_choice = case.get("expect_choice")
            got_action = "choice" if decision.get("needs_choice") else decision.get("action")
            ok = (decision.get("flow_id") == expect_flow or expect_action in ("none", ""))
            if expect_action:
                ok = ok and got_action == expect_action
            if expect_choice is not None:
                ok = ok and bool(decision.get("needs_choice")) == bool(expect_choice)
            if ok:
                case_result["passed"] += 1
            else:
                case_result["failed"].append({
                    "index": idx, "text": text, "expect": {"flow_id": expect_flow, "action": expect_action},
                    "got": {"flow_id": decision.get("flow_id"), "action": got_action,
                            "rule_id": decision.get("rule_id")},
                })
        if case_result["failed"]:
            e5.append(f"{len(case_result['failed'])} 条意图用例未通过（详见 test_cases.failed）")
    elif run_cases and not d.test_cases:
        warnings.append("该流程没有意图用例，建议至少配置办理 / 只读 / 两可各 1 条")
    checks.append(_check("5", "意图用例回归", e5))
    errors.extend(e5)

    # ── 6 入口 / 角色白名单 / 配置后台权限 ──
    e6: List[str] = []
    for ep in d.entry_points:
        path = str(ep.get("path") or "")
        if ep.get("type") == "menu" and not _PATH_RE.match(path):
            e6.append(f"入口 {ep.get('key')} 的路径必须由通用页面接管: {path}")
        for r in ep.get("visible_roles") or []:
            if str(r) not in ROLE_WHITELIST + ("admin",):
                e6.append(f"入口 {ep.get('key')} 的 visible_roles 含非白名单角色 {r}")
    for key in ("applicant_roles", "reviewer_roles"):
        for r in d.permissions.get(key) or []:
            if str(r) not in ROLE_WHITELIST:
                e6.append(f"permissions.{key} 含非白名单角色 {r}")
            if str(r) in CONFIG_ROLES:
                e6.append(f"配置管理员角色 {r} 不能出现在 {key}")
    if d.permissions.get("scope") and not re.match(r"^[a-z][a-z0-9_]{1,63}$", str(d.permissions["scope"])):
        e6.append("permissions.scope 格式不合法")
    checks.append(_check("6", "入口与角色白名单", e6))
    errors.extend(e6)

    # ── 7 抽取规则 / 日期格式 / PII 打码 ──
    e7: List[str] = []
    for f in d.fields:
        ext = f.get("extract") or {}
        key = str(f.get("key"))
        if ext.get("regex") or ext.get("builtin"):
            try:
                d._extractor_pattern(f)
            except FlowConfigError as ex:
                e7.append(f"{key}.extract: {ex}")
        for fmt in ext.get("date_formats") or []:
            try:
                if parse_dt("2026-11-25 08:30", [str(fmt)]) is None and "%" not in str(fmt):
                    e7.append(f"{key}.extract.date_formats 含无效格式 {fmt}")
            except Exception as ex:
                e7.append(f"{key}.extract.date_formats 无效: {ex}")
        if f.get("pii") and ext.get("mask") is False:
            e7.append(f"PII 字段 {key} 不允许关闭打码")
    checks.append(_check("7", "抽取规则与 PII 打码", e7))
    errors.extend(e7)

    # ── 8 审批链完整性（v3 硬约束，D7）──
    e8: List[str] = []
    reviews = [t for t in d.transitions
               if str(t.get("from")) == "pending"
               and str(t.get("to")) in ("approved", "rejected")
               and str(t.get("action")) in ("review", "approve", "reject")]
    if not reviews:
        e8.append("缺少人工审批流转：必须含 pending→approved 或 pending→rejected 的 review 流转")
    for t in reviews:
        actor = str(t.get("actor_role") or "")
        if actor not in ADMIN_LEVEL_ROLES:
            e8.append(f"review 流转的 actor_role 必须由框架强制为 admin 级，当前为 {actor or '空'}")
    for t in d.transitions:
        if str(t.get("from")) == "draft" and str(t.get("to")) == "approved":
            e8.append("禁止 draft→approved 直连（不存在提交即通过路径）")
        if str(t.get("action")) in ("review", "approve", "reject"):
            if str(t.get("actor_role") or "") not in ADMIN_LEVEL_ROLES:
                e8.append(f"流转 {t.get('from')}→{t.get('to')} 是审批动作，actor_role 必须 admin 级")
    if "submit" not in [str(t.get("action")) for t in d.transitions]:
        e8.append("缺少 draft→pending 的 submit 流转")
    checks.append(_check("8", "审批链完整性", e8))
    errors.extend(e8)

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "test_cases": case_result,
    }
