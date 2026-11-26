"""
配置元模型（docs/10 §3）—— 一份 FlowDefinition 派生出全部运行时行为。

设计要点：
- 流程是数据，不是代码；fields[] 是唯一来源，工具 schema / 提交校验 /
  确认卡 / 表单渲染 / 确定性抽取全部由它派生。
- 状态 key 是框架保留枚举，配置只能改显示名与流转子集（D9）。
- PII 打码由框架强制，配置里没有关闭开关（D7）。
- 绝不 eval / exec 配置内容；正则编译有长度上限与危险结构检查（§7.3）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

# ── 框架保留常量（配置不可改）──
FRAMEWORK_STATES: Tuple[str, ...] = ("draft", "pending", "approved", "rejected", "cancelled")
FRAMEWORK_TRANSITIONS: Tuple[Tuple[str, str, str], ...] = (
    ("draft", "pending", "submit"),
    ("pending", "approved", "review"),
    ("pending", "rejected", "review"),
    ("rejected", "draft", "reopen"),
    ("draft", "cancelled", "cancel"),
    ("pending", "cancelled", "cancel"),
)
REVIEW_ACTIONS = {"review", "approve", "reject"}
ADMIN_LEVEL_ROLES = ("admin",)
# 角色白名单：配置里的 applicant_roles / reviewer_roles / actor_role 只能取这些值；
# config_editor / config_publisher 是后台角色，禁止出现在业务流转里（§7.4）。
ROLE_WHITELIST = (
    "admin", "driver", "finance", "hr", "production", "safety",
    "quality", "general_viewer", "finance_user", "warehouse_user", "qc_user",
)
CONFIG_ROLES = ("config_editor", "config_publisher")
FIELD_TYPES = ("string", "number", "date", "datetime", "enum", "phone", "idcard", "plate", "file", "ref")
HOOK_PHASES = ("before_parse", "after_parse", "before_validate", "before_submit", "on_review")
INTENT_PHASES = ("p0_head_request", "p1_readonly", "p2_general")
INTENT_OUTCOMES = ("action", "readonly", "choice", "status", "attach")
SECTION_KEYS = (
    "meta", "fields", "intent", "attachments", "workflow",
    "session", "entry_points", "card", "prompts", "hooks", "permissions",
)
REUSE_POLICIES = ("per_application", "per_flow_reusable", "global_reusable")

MAX_REGEX_LEN = 400
# 只拦真正危险的回溯结构；非捕获组 (?:...) / 前后向断言 (?<!...) 是合法且配置里常用的，
# 不能一刀切禁止。危险结构 = 含量词的分组再被量词修饰、连续无限量词、超大重复下限。
_DANGEROUS_RE = re.compile(
    r"\([^()]*[+*][^()]*\)[+*{]"      # (a+)+ / (a*)*
    r"|(?:\.\*){2,}|(?:\.\+){2,}"       # .*.* / .+.+
    r"|\{[0-9]{4,},"                    # {10000,}
)


class FlowConfigError(ValueError):
    """配置本身不合法（编译期），区别于运行时的业务错误。"""


def compile_safe_regex(pattern: str) -> re.Pattern:
    """编译配置里的正则：长度上限 + 危险结构 + 语法校验，防 ReDoS。"""
    if not isinstance(pattern, str) or not pattern:
        raise FlowConfigError("正则必须是非空字符串")
    if len(pattern) > MAX_REGEX_LEN:
        raise FlowConfigError(f"正则过长（>{MAX_REGEX_LEN} 字符），拒绝加载")
    if _DANGEROUS_RE.search(pattern):
        raise FlowConfigError("正则含潜在灾难性回溯结构（嵌套/无限量词），拒绝加载")
    try:
        return re.compile(pattern)
    except re.error as e:
        raise FlowConfigError(f"正则无法编译: {e}") from e


def normalize_text(text: Any) -> str:
    """全角空格 / 中英标点 / 连续空白 / 小写，保证锚点与窗口稳定。"""
    q = str(text or "").replace("\u3000", " ").replace("？", "?").replace("，", ",")
    q = q.replace("！", "!").replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", " ", q).strip().lower()


def fullwidth_to_ascii(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF10 <= code <= 0xFF19:
            out.append(chr(code - 0xFEE0))
        elif ch in "：":
            out.append(":")
        elif ch == " ":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def parse_dt(value: Any, formats: Optional[Iterable[str]] = None) -> Optional[datetime]:
    """日期归一化；formats 来自 fields[].extract.date_formats，缺省用框架内置。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = fullwidth_to_ascii(str(value).strip())
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        pass
    fmts = tuple(formats or ()) or (
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
        "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
        "%m月%d日%H点", "%m月%d日 %H:%M",
    )
    for fmt in fmts:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


# ── 内置确定性抽取器（开发者一次性注册，业务从白名单里选，§1.2）──
RE_PHONE = r"(?<!\d)(1[3-9]\d{9})(?!\d)"
RE_IDCARD = r"(?<!\d)(\d{17}[\dXx])(?!\d)"
RE_PLATE = r"([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领][A-Z][A-Z0-9]{4,6})"
RE_WEIGHT = r"(\d+(?:\.\d+)?)\s*吨"
RE_DATETIME = r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)"
RE_NAME = r"(?:我叫|司机是|姓名[:：]?\s*)([\u4e00-\u9fa5]{2,4})(?![\u4e00-\u9fa5])"
RE_COMPANY = r"([\u4e00-\u9fa5]{2,12}(?:有限公司|公司|化工厂|集团))"

BUILTIN_EXTRACTORS: Dict[str, str] = {
    "phone": RE_PHONE, "idcard": RE_IDCARD, "plate": RE_PLATE,
    "weight": RE_WEIGHT, "datetime_range": RE_DATETIME,
    "name": RE_NAME, "company": RE_COMPANY,
}


def _mask(text: str, rule: Any) -> str:
    """PII 打码：默认 keep3keep4；配置可给 {head, tail} 或 'keepNkeepM'。"""
    head, tail = 3, 4
    if isinstance(rule, dict):
        head = int(rule.get("head", 3))
        tail = int(rule.get("tail", 4))
    elif isinstance(rule, str):
        m = re.fullmatch(r"keep(\d+)keep(\d+)", rule.strip())
        if m:
            head, tail = int(m.group(1)), int(m.group(2))
    if len(text) <= head + tail:
        return "*" * len(text)
    return text[:head] + "*" * (len(text) - head - tail) + text[-tail:]


class FlowDefinition:
    """一份已发布（或草稿）流程配置；所有方法都是纯派生，不写库。"""

    def __init__(self, raw: Dict[str, Any]):
        if not isinstance(raw, dict):
            raise FlowConfigError("流程定义必须是 JSON 对象")
        self.raw = raw
        self._compiled: Dict[str, List[re.Pattern]] = {}

    # ── 段访问 ──
    @property
    def meta(self) -> Dict[str, Any]:
        return self.raw.get("meta") or {}

    @property
    def flow_id(self) -> str:
        return str(self.meta.get("flow_id") or "")

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.flow_id or "未命名流程")

    @property
    def version(self) -> int:
        return int(self.meta.get("version") or 0)

    @property
    def fields(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("fields") or [])

    @property
    def groups(self) -> List[Dict[str, Any]]:
        groups = self.raw.get("groups") or []
        if not groups:
            keys = []
            for f in self.fields:
                g = str(f.get("group") or "default")
                if g not in keys:
                    keys.append(g)
            groups = [{"key": k, "label": k} for k in keys]
        return list(groups)

    @property
    def field_by_key(self) -> Dict[str, Dict[str, Any]]:
        return {str(f["key"]): f for f in self.fields if f.get("key")}

    @property
    def field_keys(self) -> set:
        return set(self.field_by_key)

    @property
    def pii_keys(self) -> set:
        return {k for k, f in self.field_by_key.items() if f.get("pii")}

    @property
    def attachments(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("attachments") or [])

    @property
    def attachment_kinds(self) -> Dict[str, str]:
        return {str(a["key"]): str(a.get("label") or a["key"]) for a in self.attachments if a.get("key")}

    @property
    def attachment_by_key(self) -> Dict[str, Dict[str, Any]]:
        return {str(a["key"]): a for a in self.attachments if a.get("key")}

    @property
    def intent(self) -> Dict[str, Any]:
        return self.raw.get("intent") or {}

    @property
    def workflow(self) -> Dict[str, Any]:
        return self.raw.get("workflow") or {}

    @property
    def session(self) -> Dict[str, Any]:
        return self.raw.get("session") or {}

    @property
    def entry_points(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("entry_points") or [])

    @property
    def card(self) -> Dict[str, Any]:
        return self.raw.get("card") or {}

    @property
    def prompts(self) -> Dict[str, Any]:
        return self.raw.get("prompts") or {}

    @property
    def hooks(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("hooks") or [])

    @property
    def permissions(self) -> Dict[str, Any]:
        return self.raw.get("permissions") or {}

    @property
    def state_labels(self) -> Dict[str, str]:
        labels = {s: s for s in FRAMEWORK_STATES}
        labels.update({k: str(v) for k, v in (self.workflow.get("state_labels") or {}).items()
                       if k in FRAMEWORK_STATES})
        return labels

    @property
    def transitions(self) -> List[Dict[str, Any]]:
        return list(self.workflow.get("allowed_transitions") or [])

    @property
    def classifier(self) -> Dict[str, Any]:
        return dict(self.intent.get("classifier") or {"mode": "rules", "fallback": "rules"})

    @property
    def test_cases(self) -> List[Dict[str, Any]]:
        return list(self.intent.get("test_cases") or [])

    @property
    def session_prefix(self) -> str:
        return str(self.session.get("session_prefix") or f"flow-{self.flow_id}-")

    @property
    def ttl_hours(self) -> int:
        try:
            return max(1, min(int(self.session.get("ttl_hours", 2)), 168))
        except (TypeError, ValueError):
            return 2

    @property
    def scope(self) -> str:
        return str(self.permissions.get("scope") or "general")

    @property
    def applicant_roles(self) -> List[str]:
        return [str(r) for r in (self.permissions.get("applicant_roles") or [])]

    @property
    def reviewer_roles(self) -> List[str]:
        return [str(r) for r in (self.permissions.get("reviewer_roles") or ["admin"])]

    # ── 槽位清洗 / 合并 ──
    def sanitize_slots(self, payload: Any) -> Dict[str, Any]:
        """白名单过滤：模型幻觉字段、非法类型、越权附件引用一律丢掉。"""
        if not isinstance(payload, dict):
            return {}
        out: Dict[str, Any] = {}
        for key in self.field_keys:
            if key not in payload:
                continue
            v = payload[key]
            if v is None or isinstance(v, (dict, list)):
                continue
            out[key] = str(v).strip() if isinstance(v, str) else v
        raw_att = payload.get("attachments")
        if isinstance(raw_att, dict):
            atts = {}
            for kind in self.attachment_kinds:
                v = raw_att.get(kind)
                if isinstance(v, str) and v.strip():
                    atts[kind] = v.strip()
            if atts:
                out["attachments"] = atts
        return out

    def merge_slots(self, base: Any, updates: Any) -> Dict[str, Any]:
        merged = self.sanitize_slots(base)
        new = self.sanitize_slots(updates)
        if "attachments" in new:
            atts = dict(merged.get("attachments") or {})
            atts.update(new.pop("attachments"))
            merged.pop("attachments", None)
            if atts:
                merged["attachments"] = atts
        merged.update(new)
        return merged

    # ── 校验 ──
    def _is_visible(self, field: Dict[str, Any], form: Dict[str, Any]) -> bool:
        cond = field.get("visible_when")
        if not isinstance(cond, dict) or not cond:
            return True
        target = str(cond.get("field") or "")
        expected = cond.get("eq", cond.get("in"))
        actual = str(form.get(target) or "")
        if isinstance(expected, list):
            return actual in [str(x) for x in expected]
        return actual == str(expected)

    def _field_patterns(self, field: Dict[str, Any]) -> List[re.Pattern]:
        """缓存编译结果，避免每次校验重编译。"""
        key = str(field.get("key"))
        if key not in self._compiled:
            pats = []
            validation = field.get("validation") or {}
            if validation.get("regex"):
                pats.append(compile_safe_regex(str(validation["regex"])))
            pats.extend(_TYPE_PATTERNS.get(str(field.get("type")), []))
            self._compiled[key] = pats
        return self._compiled[key]

    def missing_labels(self, form_data: Any) -> List[str]:
        form = self.sanitize_slots(form_data)
        missing: List[str] = []
        for f in self.fields:
            if not f.get("required") or not self._is_visible(f, form):
                continue
            if str(form.get(f["key"]) or "").strip() == "":
                missing.append(str(f.get("label") or f["key"]))
        atts = form.get("attachments") or {}
        for a in self.attachments:
            if a.get("required") and not str(atts.get(a["key"]) or "").strip():
                missing.append(str(a.get("label") or a["key"]))
        return missing

    def validate(self, form_data: Any, *, for_submit: bool = False,
                 run_hooks: bool = True, now: Optional[datetime] = None) -> Dict[str, Any]:
        """确定性校验：必填 / 类型 / 正则 / 范围 / 枚举 / 附件数量 + 白名单 hook。"""
        form = self.sanitize_slots(form_data)
        missing = self.missing_labels(form)
        errors: List[str] = []
        for f in self.fields:
            key = str(f.get("key"))
            value = form.get(key)
            if value is None or str(value).strip() == "" or not self._is_visible(f, form):
                continue
            text = str(value).strip()
            ftype = str(f.get("type") or "string")
            validation = f.get("validation") or {}
            if ftype == "number":
                try:
                    num = float(text)
                    if "min" in validation and num < float(validation["min"]):
                        errors.append(f"{f.get('label')}不能小于 {validation['min']}")
                    if "max" in validation and num > float(validation["max"]):
                        errors.append(f"{f.get('label')}不能超过 {validation['max']}")
                except (TypeError, ValueError):
                    errors.append(f"{f.get('label')}必须是数字")
            elif ftype in ("date", "datetime"):
                fmts = (f.get("extract") or {}).get("date_formats")
                if parse_dt(text, fmts) is None:
                    errors.append(f"{f.get('label')}格式无法识别")
            elif ftype == "enum" and f.get("options"):
                if text not in [str(o) for o in f["options"]]:
                    errors.append(f"{f.get('label')}只能是：{'、'.join(str(o) for o in f['options'])}")
            for pattern in self._field_patterns(f):
                if not pattern.search(text):
                    errors.append(f"{f.get('label')}格式不正确")
                    break
            if "max_length" in validation and len(text) > int(validation["max_length"]):
                errors.append(f"{f.get('label')}过长（最多 {validation['max_length']} 字）")
        # 附件数量上限
        atts = form.get("attachments") or {}
        for a in self.attachments:
            if not a.get("max_count"):
                continue
            ids = atts.get(a["key"])
            count = len(ids) if isinstance(ids, list) else (1 if ids else 0)
            if count > int(a["max_count"]):
                errors.append(f"{a.get('label')}最多 {a['max_count']} 个")

        if run_hooks:
            try:
                from .hooks import run_phase
                errors.extend(run_phase(self, "before_validate", {
                    "form_data": form, "for_submit": for_submit,
                    "now": (now or datetime.now()).isoformat(),
                }))
            except Exception as e:  # hook 异常不能让全站校验失效，转成显式错误
                errors.append(f"扩展点校验执行失败: {e}")

        dedup = list(dict.fromkeys(errors))
        return {"missing": missing, "errors": dedup, "complete": not missing and not dedup}

    # ── 确定性抽取（fields[].extract）──
    def _extractor_pattern(self, field: Dict[str, Any]) -> Optional[re.Pattern]:
        ext = field.get("extract") or {}
        pattern = ext.get("regex")
        if not pattern and ext.get("builtin"):
            pattern = BUILTIN_EXTRACTORS.get(str(ext["builtin"]))
        if not pattern:
            return None
        return compile_safe_regex(str(pattern))

    def extract_by_rules(self, text: str) -> Dict[str, Any]:
        """按配置逐字段抽取；先确定性规则，再交模型补缺（§5.1）。"""
        if not text:
            return {}
        raw = fullwidth_to_ascii(str(text))
        out: Dict[str, Any] = {}
        for f in self.fields:
            ext = f.get("extract") or {}
            key = str(f.get("key"))
            builtin = str(ext.get("builtin") or "")
            if builtin == "datetime_range":
                pattern = self._extractor_pattern(f)
                if pattern is None:
                    continue
                hits = [parse_dt(t, ext.get("date_formats")) for t in pattern.findall(raw)]
                hits = sorted({t for t in hits if t})
                targets = list(ext.get("targets") or [key])
                for i, target in enumerate(targets):
                    if i < len(hits) and target in self.field_keys:
                        out[target] = hits[i].strftime("%Y-%m-%d %H:%M")
                continue
            if builtin == "gender":
                if "女" in raw:
                    out[key] = "女"
                elif "男" in raw:
                    out[key] = "男"
                continue
            if ext.get("keywords"):
                for item in ext["keywords"]:
                    if not isinstance(item, dict):
                        continue
                    if any(str(w) in raw for w in (item.get("any") or [])):
                        out[key] = item.get("value")
                        break
            pattern = self._extractor_pattern(f)
            if pattern is None:
                continue
            group = int(ext.get("group", 1) or 1)
            m = pattern.search(raw)
            if not m:
                continue
            try:
                value = m.group(group)
            except IndexError:
                value = m.group(0)
            if ext.get("uppercase"):
                value = str(value).upper()
            out[key] = value
        return out

    def reconcile_high_confidence(self, incoming: Dict[str, Any],
                                  rule_based: Dict[str, Any], text: str) -> Dict[str, Any]:
        """模型高置信值必须在原文独立出现，否则用规则值校正 / 剔除（防假手机号）。"""
        out = dict(incoming)
        if not text:
            return out
        raw = str(text)
        for f in self.fields:
            ext = f.get("extract") or {}
            if ext.get("reconcile") != "must_appear_in_text":
                continue
            key = str(f.get("key"))
            given = str(out.get(key) or "").strip()
            if not given:
                continue
            pattern = self._extractor_pattern(f)
            if pattern is None:
                continue
            matches = {str(m).upper() for m in pattern.findall(raw)}
            if given.upper() in matches:
                continue
            if rule_based.get(key):
                out[key] = rule_based[key]
            else:
                out.pop(key, None)
        return out

    # ── 展示（PII 打码强制）──
    def mask_value(self, key: str, value: Any) -> str:
        text = str(value if value is not None else "")
        if key in self.pii_keys and text:
            f = self.field_by_key.get(key, {})
            return _mask(text, (f.get("extract") or {}).get("mask") or "keep3keep4")
        return text

    def build_card(self, slots: Any, *, masked: bool = True) -> Dict[str, Any]:
        form = self.sanitize_slots(slots)
        wanted = [str(k) for k in (self.card.get("field_keys") or [])] or [
            str(f["key"]) for f in sorted(self.fields, key=lambda x: int(x.get("order") or 0))
        ]
        groups = []
        for g in self.groups:
            items = []
            for key in wanted:
                f = self.field_by_key.get(key)
                if not f or str(f.get("group") or "default") != str(g.get("key")):
                    continue
                raw_value = form.get(key, "")
                value = self.mask_value(key, raw_value) if masked else str(raw_value or "")
                items.append({"key": key, "label": str(f.get("label") or key),
                              "value": value, "filled": bool(str(raw_value or "").strip())})
            if items:
                groups.append({"key": str(g.get("key")), "label": str(g.get("label")), "items": items})
        atts = form.get("attachments") or {}
        attachments = [
            {"key": str(a["key"]), "label": str(a.get("label") or a["key"]),
             "filled": bool(atts.get(a["key"])), "id": atts.get(a["key"], "")}
            for a in self.attachments
        ]
        return {"title": str(self.card.get("title") or f"{self.name}信息核对"),
                "groups": groups, "attachments": attachments}

    def build_payload(self, slots: Any, *, for_submit: bool = False,
                      note: str = "") -> Dict[str, Any]:
        form = self.sanitize_slots(slots)
        check = self.validate(form, for_submit=for_submit)
        payload: Dict[str, Any] = {
            "__flow_payload__": True,
            "__entry_payload__": True,  # 兼容既有 generate_node 的提取逻辑
            "flow_id": self.flow_id,
            "definition_version": self.version,
            "slots": form,
            "missing": check["missing"],
            "errors": check["errors"],
            "complete": check["complete"],
            "card": self.build_card(form) if check["complete"] else None,
        }
        if note:
            payload["note"] = note
        if check["complete"]:
            payload["hint"] = (
                "信息已齐备，请告知用户核对确认卡后点击「确认提交」或「去表单细改」；"
                "AI 不会代替提交，也不会写入数据库。"
            )
        else:
            first = (check["missing"] + check["errors"])[:4]
            payload["hint"] = (
                "还缺：" + "、".join(first) + "。请用自然的口语一次性询问这几项，"
                "不要逐条追问，也不要编造任何字段值。"
            )
        return payload

    def llm_slots_schema(self) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        for f in self.fields:
            desc = str(f.get("label") or f["key"])
            if f.get("placeholder"):
                desc += f"（{f['placeholder']}）"
            item: Dict[str, Any] = {"type": "string", "description": desc}
            if f.get("options"):
                item["enum"] = [str(o) for o in f["options"]]
            props[str(f["key"])] = item
        return {"type": "object", "properties": props, "additionalProperties": False}

    def describe_fields_for_prompt(self) -> str:
        return "\n".join(f"- {f['key']}（{f.get('label') or f['key']}）" for f in self.fields)

    def system_prompt(self) -> str:
        custom = str(self.prompts.get("system_prompt") or "").strip()
        if custom:
            base = custom
        else:
            base = (
                f"你是园区「{self.name}」助手，只负责收集与核对信息，"
                "不办理审批、不提交申请、不写入数据库。\n"
                "工作规则：\n"
                "1. 用户提供任何新信息时先调用 parse_flow_materials 合并字段。\n"
                "2. 需要附件时调用 get_my_attachments。\n"
                "3. 回答「还缺什么」或收口前必须调用 check_flow_rules，不要自行判断。\n"
                "4. 只有 complete=true 才调用 push_flow_confirm_card。\n"
                "5. 缺项时自然口语一次性问清（最多 4 项），禁止编造任何字段值。\n"
                "6. 敏感信息不要完整复述，确认卡会自动打码。\n"
                "7. 明确告知用户需本人点击提交，AI 不会代替提交。\n"
            )
        hints = self.prompts.get("ask_hints") or []
        if hints:
            base += "\n补充话术：\n" + "\n".join(f"- {h}" for h in hints)
        return base + "\n\n可收集字段：\n" + self.describe_fields_for_prompt()

    # ── 前端渲染元数据（不含判定规则 / 否定词 / 用例 / PII 值，§5.2）──
    def render_config(self) -> Dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "name": self.name,
            "version": self.version,
            "fields": [
                {k: f.get(k) for k in ("key", "label", "group", "type", "required", "options",
                                       "placeholder", "pii", "visible_when", "order") if k in f}
                for f in sorted(self.fields, key=lambda x: int(x.get("order") or 0))
            ],
            "groups": self.groups,
            "attachments": [
                {k: a.get(k) for k in ("key", "label", "required", "max_count", "accept",
                                       "max_size_mb", "reuse_policy") if k in a}
                for a in self.attachments
            ],
            "card": {"title": self.card.get("title") or f"{self.name}信息核对",
                     "field_keys": self.card.get("field_keys") or [str(f["key"]) for f in self.fields],
                     "prefill_target": self.card.get("prefill_target") or f"/flows/{self.flow_id}/apply"},
            "state_labels": self.state_labels,
            "session_prefix": self.session_prefix,
            "scope": self.scope,
        }

    def entry_points_for(self, role: str) -> List[Dict[str, Any]]:
        """按角色过滤入口；admin 可见全部。前端拉这个结果，不硬编码 flow_id。"""
        result = []
        for ep in self.entry_points:
            roles = [str(r) for r in (ep.get("visible_roles") or [])]
            if roles and role != "admin" and role not in roles:
                continue
            result.append(dict(ep))
        return result

    # ── 状态机 ──
    def find_transition(self, action: str, from_status: str, to_status: str) -> Optional[Dict[str, Any]]:
        for t in self.transitions:
            if (str(t.get("action")) == action
                    and str(t.get("from")) == from_status
                    and str(t.get("to")) == to_status):
                return t
        return None

    def hook_refs(self, phase: str) -> List[Dict[str, Any]]:
        return [h for h in self.hooks if str(h.get("phase")) == phase]


_TYPE_PATTERNS: Dict[str, List[re.Pattern]] = {
    "phone": [re.compile(r"^1[3-9]\d{9}$")],
    "idcard": [re.compile(r"^\d{17}[\dXx]$")],
    "plate": [re.compile(r"^[一-龥][A-Z][A-Z0-9]{4,6}$")],
}
