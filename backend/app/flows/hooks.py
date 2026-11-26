"""
命名扩展点白名单（docs/10 §7）—— 开发者注册，业务同事只能从白名单里「选」。

安全底线：
- 配置里只能写 {name, phase, params}，绝不 eval / exec 配置内容；
- params 只接受字面量，不允许变量引用 / 模板 / 表达式；
- hook 只返回结构化结果，不能直接写库；
- 三态生命周期 active → deprecated → retired（D16）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .model import FlowConfigError, FlowDefinition, parse_dt, to_json

logger = logging.getLogger(__name__)

HOOK_LIFECYCLE: Dict[str, str] = {}
_HOOKS: Dict[str, Dict[str, Any]] = {}

_FORBIDDEN_PARAM_TEXT = re.compile(r"\$\{|\{\{|__import__|eval\s*\(|exec\s*\(|lambda\s|os\.|sys\.|subprocess")


def register_hook(name: str, fn: Callable, *, phase: str, description: str,
                  params_schema: Optional[Dict[str, Any]] = None,
                  lifecycle: str = "active") -> None:
    """注册一个扩展点。重复注册以最后一次为准（便于测试覆盖）。"""
    from .model import HOOK_PHASES
    if phase not in HOOK_PHASES:
        raise FlowConfigError(f"未知 hook phase: {phase}")
    _HOOKS[name] = {
        "name": name, "fn": fn, "phase": phase, "description": description,
        "params_schema": params_schema or {"type": "object", "properties": {}, "additionalProperties": False},
    }
    HOOK_LIFECYCLE.setdefault(name, lifecycle)


def get_hook(name: str) -> Optional[Dict[str, Any]]:
    return _HOOKS.get(name)


def list_hooks() -> List[Dict[str, Any]]:
    return [
        {
            "name": h["name"], "phase": h["phase"], "description": h["description"],
            "params_schema": h["params_schema"],
            "lifecycle": HOOK_LIFECYCLE.get(h["name"], "active"),
        }
        for h in _HOOKS.values()
    ]


def set_lifecycle(name: str, state: str) -> None:
    if name not in _HOOKS:
        raise FlowConfigError(f"未注册的 hook: {name}")
    if state not in ("active", "deprecated", "retired"):
        raise FlowConfigError("生命周期只能是 active / deprecated / retired")
    HOOK_LIFECYCLE[name] = state


def validate_params(name: str, params: Any) -> List[str]:
    """校验 params 的类型与值域（§7.1）：只许字面量，值域由 params_schema 定。"""
    hook = _HOOKS.get(name)
    if hook is None:
        return [f"未注册的扩展点: {name}"]
    errors: List[str] = []
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return [f"{name} 的 params 必须是对象"]
    schema = hook["params_schema"]
    props = schema.get("properties") or {}
    for key in schema.get("required") or []:
        if key not in params:
            errors.append(f"{name} 缺少必填参数 {key}")
    for key, value in params.items():
        if key not in props:
            if schema.get("additionalProperties") is False:
                errors.append(f"{name} 不接受参数 {key}")
            continue
        rule = props[key] or {}
        if isinstance(value, (dict, list)) and not isinstance(value, (str, bytes)):
            # 允许字面量数组，但元素仍须是标量
            if isinstance(value, list) and all(isinstance(x, (str, int, float, bool)) or x is None for x in value):
                pass
            else:
                errors.append(f"{name}.{key} 只允许标量字面量或标量数组")
                continue
        if isinstance(value, str) and _FORBIDDEN_PARAM_TEXT.search(value):
            errors.append(f"{name}.{key} 含表达式 / 脚本特征，拒绝发布")
            continue
        expected = rule.get("type")
        if expected and not _type_matches(value, expected):
            errors.append(f"{name}.{key} 类型应为 {expected}")
            continue
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                errors.append(f"{name}.{key} 不能小于 {rule['minimum']}")
            if "maximum" in rule and value > rule["maximum"]:
                errors.append(f"{name}.{key} 不能大于 {rule['maximum']}")
        if "enum" in rule and value not in rule["enum"]:
            errors.append(f"{name}.{key} 只能是 {rule['enum']}")
    return errors


def _type_matches(value: Any, expected: str) -> bool:
    return {
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "array": isinstance(value, list),
    }.get(expected, True)


def assert_can_reference(name: str) -> None:
    state = HOOK_LIFECYCLE.get(name, "active")
    if state in ("deprecated", "retired"):
        raise FlowConfigError(f"扩展点 {name} 已 {state}，禁止在新配置 / 新版发布中引用")


def assert_can_retire(name: str, *, published_references: int, open_applications: int) -> None:
    """D16：retired 必须 ① 全量扫描已发布版本无引用 ② 关联单据过保留期 ③ dry-run + 审计。"""
    if published_references > 0:
        raise FlowConfigError(f"仍有 {published_references} 个已发布版本引用 {name}，不能下线")
    if open_applications > 0:
        raise FlowConfigError(f"仍有 {open_applications} 张在途单据关联 {name}，不能下线")


def run_phase(definition: FlowDefinition, phase: str, ctx: Dict[str, Any]) -> List[str]:
    """执行 before_validate 类 hook，汇总错误文案。"""
    errors: List[str] = []
    for ref in definition.hook_refs(phase):
        hook = _HOOKS.get(str(ref.get("name")))
        if hook is None:
            errors.append(f"扩展点 {ref.get('name')} 未注册")
            continue
        if HOOK_LIFECYCLE.get(hook["name"]) == "retired":
            errors.append(f"扩展点 {hook['name']} 已下线，请联系管理员处理")
            continue
        try:
            result = hook["fn"](definition, ctx, ref.get("params") or {}) or {}
            errors.extend([str(e) for e in (result.get("errors") or [])])
        except Exception as e:
            logger.warning(f"[flow_hooks] {hook['name']} 执行失败: {e}")
            errors.append(f"扩展点 {hook['name']} 执行失败: {e}")
    return errors


def run_review_hooks(definition: FlowDefinition, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """on_review：产出写进 result JSONB（编号 / 二维码等），不直接写库。"""
    merged: Dict[str, Any] = {}
    for ref in definition.hook_refs("on_review"):
        hook = _HOOKS.get(str(ref.get("name")))
        if hook is None or HOOK_LIFECYCLE.get(hook["name"]) == "retired":
            continue
        try:
            result = hook["fn"](definition, ctx, ref.get("params") or {}) or {}
            merged.update(result.get("result") or {})
        except Exception as e:
            logger.warning(f"[flow_hooks] on_review {hook['name']} 失败: {e}")
    return merged


# ── 首批 6 个 hook：全部来自入园现有逻辑（§7.2）──

def _h_reconcile(definition: FlowDefinition, ctx: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    corrected = definition.reconcile_high_confidence(
        ctx.get("incoming") or {}, ctx.get("rule_based") or {}, ctx.get("text") or ""
    )
    return {"slots": corrected}


def _h_time_window(definition: FlowDefinition, ctx: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """入园 / 出园时间先后与合理性（复刻 entry_schema.validate 的原文案）。"""
    form = ctx.get("form_data") or {}
    in_key = str(params.get("in_field") or "entry_time")
    out_key = str(params.get("out_field") or "exit_time")
    errors: List[str] = []
    t_in = parse_dt(form.get(in_key))
    t_out = parse_dt(form.get(out_key))
    if form.get(in_key) and t_in is None:
        errors.append("入园时间格式无法识别，请用 YYYY-MM-DD HH:mm")
    if form.get(out_key) and t_out is None:
        errors.append("出园时间格式无法识别，请用 YYYY-MM-DD HH:mm")
    if t_in and t_out and t_out <= t_in:
        errors.append("出园时间必须晚于入园时间")
    if ctx.get("for_submit") and t_in:
        now_raw = ctx.get("now")
        now = datetime.fromisoformat(now_raw) if isinstance(now_raw, str) else datetime.now()
        if t_in < now:
            errors.append("入园时间不能早于当前时间，请重新选择")
    return {"errors": errors}


def _h_weight_limit(definition: FlowDefinition, ctx: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    form = ctx.get("form_data") or {}
    field = str(params.get("field") or "weight_ton")
    limit = float(params.get("max") or 100.0)
    raw = str(form.get(field) or "").strip()
    errors: List[str] = []
    if raw:
        try:
            w = float(raw)
            if w <= 0:
                errors.append("运输重量必须大于 0 吨")
            elif w > limit:
                errors.append(f"运输重量不能超过 {limit:g} 吨")
        except ValueError:
            errors.append("运输重量必须是数字（吨）")
    return {"errors": errors}


def _h_attachment_binding(definition: FlowDefinition, ctx: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """结构性前置检查；真正的归属与防串图在 service 事务里强制（框架硬约束）。"""
    form = ctx.get("form_data") or {}
    atts = form.get("attachments") or {}
    errors = []
    for a in definition.attachments:
        if a.get("required") and not str(atts.get(a["key"]) or "").strip():
            errors.append(f"缺少{definition.attachment_kinds.get(str(a['key']), a['key'])}")
    return {"errors": errors}


def _h_pass_no(definition: FlowDefinition, ctx: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """审核通过生成编号 + 二维码；只返回结构化数据，由 service 写 result JSONB。"""
    if not ctx.get("approved"):
        return {}
    import base64
    import uuid

    form = ctx.get("form_data") or {}
    app = ctx.get("application") or {}
    prefix = str(params.get("prefix") or "GP")
    pass_no = prefix + datetime.now().strftime("%Y%m%d") + uuid.uuid4().hex[:8].upper()
    parts = [str(params.get("payload_prefix") or "FLOW-PASS"), pass_no,
             str(app.get("id") or "")]
    for key in (params.get("payload_fields") or []):
        parts.append(str(form.get(key) or ""))
    payload = "|".join(parts)
    qr_svg = ""
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
        from qrcode.image.svg import SvgPathImage
        qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=8, border=2)
        qr.add_data(payload)
        qr.make(fit=True)
        raw = qr.make_image(image_factory=SvgPathImage).to_string()
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        qr_svg = "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
    except Exception as e:  # pragma: no cover - 依赖缺失时降级
        logger.warning(f"二维码生成失败（不影响编号）: {e}")
    return {"result": {"pass_no": pass_no, "qr_payload": payload, "qr_svg": qr_svg,
                       "pass_issued_at": datetime.now().isoformat()}}


def _h_erp_stock(definition: FlowDefinition, ctx: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """阶段 4 预留：未接 ERP 时不做任何外部调用，也不放行写入。"""
    return {"errors": [], "note": "ERP 适配器尚未接入（阶段 4）"}


def register_builtin_hooks() -> None:
    register_hook(
        "reconcile_high_confidence", _h_reconcile, phase="after_parse",
        description="模型高置信字段必须在原文独立出现，否则用规则值校正或剔除",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    register_hook(
        "validate_time_window", _h_time_window, phase="before_validate",
        description="校验起止时间格式、先后关系，提交时不得早于当前",
        params_schema={"type": "object", "properties": {
            "in_field": {"type": "string"}, "out_field": {"type": "string"},
        }, "additionalProperties": False},
    )
    register_hook(
        "validate_weight_limit", _h_weight_limit, phase="before_validate",
        description="重量必须大于 0 且不超过配置上限",
        params_schema={"type": "object", "properties": {
            "field": {"type": "string"},
            "max": {"type": "number", "minimum": 0.1, "maximum": 1000},
        }, "additionalProperties": False},
    )
    register_hook(
        "validate_attachment_binding", _h_attachment_binding, phase="before_submit",
        description="提交前检查必交附件；归属与防串图由框架事务强制",
        params_schema={"type": "object", "properties": {}, "additionalProperties": False},
    )
    register_hook(
        "generate_pass_no_and_qr", _h_pass_no, phase="on_review",
        description="审核通过生成编号与二维码，产出写入 result JSONB",
        params_schema={"type": "object", "properties": {
            "prefix": {"type": "string"}, "payload_prefix": {"type": "string"},
            "payload_fields": {"type": "array"},
        }, "additionalProperties": False},
    )
    register_hook(
        "erp_read_stock", _h_erp_stock, phase="before_validate",
        description="预留：接 ERP 后只读查库存（阶段 4 + MCP 适配）",
        params_schema={"type": "object", "properties": {
            "material_field": {"type": "string"},
        }, "additionalProperties": False},
        lifecycle="deprecated",  # 预留能力，新配置默认不允许引用
    )


register_builtin_hooks()
