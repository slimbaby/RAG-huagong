"""
司机入园预约 —— 字段定义与校验规则的单一来源（single source of truth）

原先 18 个字段、中文标签、4 个分组、重量/时间规则散落在
driver_entry.yaml / entry_service.py / 前端 api/entry.ts / 表单页四处。
方案乙不再使用 workflow 引擎，所有消费方统一从这里取：

- 后端提交校验、必填判断      → validate() / missing_labels()
- AI 解析工具的 JSON Schema    → llm_slots_schema() / FIELD_KEYS
- 确认卡与预填表单的字段清单  → FORM_FIELDS / FIELD_GROUPS
- 附件种类                    → ATTACHMENT_KINDS

设计约束：
- AI 只产出这里的字段，不写库、不新增字段；
- 校验规则是确定性代码，不由模型判断是否齐备；
- 敏感字段（身份证号、手机号）在确认卡里默认打码展示。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Tuple

# ── 分组 ──
FIELD_GROUPS: List[Dict[str, str]] = [
    {"key": "driver", "label": "司机信息"},
    {"key": "vehicle", "label": "车辆信息"},
    {"key": "apply", "label": "运输与申请信息"},
    {"key": "attachments", "label": "车辆照片与证件"},
]

# ── 文本字段（13 项，全部必填；化学品三项按用户决策保留必填）──
FORM_FIELDS: List[Dict[str, Any]] = [
    {"key": "driver_name", "label": "司机姓名", "group": "driver", "placeholder": "张三"},
    {"key": "gender", "label": "性别", "group": "driver", "placeholder": "男 / 女", "options": ["男", "女"]},
    {"key": "id_no", "label": "身份证号", "group": "driver", "placeholder": "18 位身份证号码", "pii": True},
    {"key": "phone", "label": "手机号", "group": "driver", "placeholder": "13800000000", "pii": True},
    {"key": "plate_no", "label": "车牌号", "group": "vehicle", "placeholder": "鲁A12345"},
    {"key": "vehicle_type", "label": "车辆类型", "group": "vehicle", "placeholder": "如 罐式货车 / 槽罐车"},
    {"key": "load_capacity", "label": "车辆承重", "group": "vehicle", "placeholder": "如 30吨"},
    {"key": "company_name", "label": "申请公司名称", "group": "apply", "placeholder": "XX化工有限公司"},
    {"key": "chemical_name", "label": "运输化学品名称", "group": "apply", "placeholder": "如 甲醇"},
    {"key": "chemical_type", "label": "化学品类型", "group": "apply", "placeholder": "如 易燃液体"},
    {"key": "weight_ton", "label": "运输重量（吨）", "group": "apply", "placeholder": "仅填数字，如 20"},
    {"key": "entry_time", "label": "入园时间", "group": "apply", "placeholder": "2026-11-26 08:00"},
    {"key": "exit_time", "label": "出园时间", "group": "apply", "placeholder": "2026-11-26 18:00"},
]

# ── 五类必交照片 / 证件 ──
ATTACHMENT_KINDS: Dict[str, str] = {
    "vehicle_front": "车头照片",
    "vehicle_rear": "车尾照片",
    "vehicle_license": "行驶证",
    "driving_license": "驾驶证",
    "hazmat_license": "危化品运输证",
}

FIELD_KEYS = {f["key"] for f in FORM_FIELDS}
FIELD_BY_KEY = {f["key"]: f for f in FORM_FIELDS}
PII_FIELDS = {f["key"] for f in FORM_FIELDS if f.get("pii")}
REQUIRED_FIELDS: List[Tuple[str, str]] = [(f["key"], f["label"]) for f in FORM_FIELDS]

MAX_WEIGHT_TON = 100.0

# 时间输入兼容：ISO / 中文 / 斜杠 / 仅日期
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
)


def parse_dt(value: Any) -> datetime | None:
    """归一化为 naive datetime；无法解析返回 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        pass
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def sanitize_slots(payload: Any) -> Dict[str, Any]:
    """清洗 AI / 前端传来的字段：只保留白名单，附件 ID 只保留合法 kind。

    防止模型幻觉出额外字段、或把任意字符串塞进 attachments 造成越权引用。
    """
    if not isinstance(payload, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in FIELD_KEYS:
        if key not in payload:
            continue
        v = payload[key]
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        out[key] = str(v).strip() if not isinstance(v, (int, float)) else v
    raw_att = payload.get("attachments")
    if isinstance(raw_att, dict):
        atts = {}
        for kind in ATTACHMENT_KINDS:
            v = raw_att.get(kind)
            if isinstance(v, str) and v.strip():
                atts[kind] = v.strip()
        if atts:
            out["attachments"] = atts
    return out


def merge_slots(base: Any, updates: Any) -> Dict[str, Any]:
    """合并「已有字段」与「本轮新增」。

    新值非空才覆盖；新值为空字符串表示司机明确改口清空，同样生效。
    附件按 kind 逐项合并，不会丢掉已上传的其它四类。
    """
    merged = sanitize_slots(base)
    new = sanitize_slots(updates)
    if "attachments" in new:
        atts = dict(merged.get("attachments") or {})
        atts.update(new.pop("attachments"))
        merged = {k: v for k, v in merged.items() if k != "attachments"}
        if atts:
            merged["attachments"] = atts
    merged.update(new)
    return merged


def missing_labels(form_data: Any) -> List[str]:
    """返回缺失项的中文标签（文本字段 + 五类附件）。"""
    form = sanitize_slots(form_data)
    missing = [
        f["label"] for f in FORM_FIELDS
        if str(form.get(f["key"]) or "").strip() == ""
    ]
    atts = form.get("attachments") or {}
    for kind, label in ATTACHMENT_KINDS.items():
        if not str(atts.get(kind) or "").strip():
            missing.append(label)
    return missing


def validate(form_data: Any, *, for_submit: bool = False,
             now: datetime | None = None) -> Dict[str, Any]:
    """确定性校验。返回 {missing, errors, complete}。

    for_submit=True 时额外校验「入园时间不得早于当前」；编辑草稿不校验，
    避免司机保存一份尚未到期的草稿被拦下。
    """
    form = sanitize_slots(form_data)
    missing = missing_labels(form)
    errors: List[str] = []

    raw_weight = str(form.get("weight_ton") or "").strip()
    if raw_weight:
        try:
            w = float(raw_weight)
            if w <= 0:
                errors.append("运输重量必须大于 0 吨")
            elif w > MAX_WEIGHT_TON:
                errors.append(f"运输重量不能超过 {MAX_WEIGHT_TON:g} 吨")
        except ValueError:
            errors.append("运输重量必须是数字（吨）")

    t_in = parse_dt(form.get("entry_time"))
    t_out = parse_dt(form.get("exit_time"))
    if form.get("entry_time") and t_in is None:
        errors.append("入园时间格式无法识别，请用 YYYY-MM-DD HH:mm")
    if form.get("exit_time") and t_out is None:
        errors.append("出园时间格式无法识别，请用 YYYY-MM-DD HH:mm")
    if t_in and t_out and t_out <= t_in:
        errors.append("出园时间必须晚于入园时间")
    if for_submit and t_in and t_in < (now or datetime.now()):
        errors.append("入园时间不能早于当前时间，请重新选择")

    return {
        "missing": missing,
        "errors": errors,
        "complete": not missing and not errors,
    }


def mask_value(key: str, value: Any) -> str:
    """确认卡上的敏感值默认打码；完整值只在司机浏览器内存里，不落消息快照。"""
    text = str(value if value is not None else "")
    if key in PII_FIELDS and len(text) > 6:
        return text[:3] + "*" * (len(text) - 7) + text[-4:]
    return text


def build_card(slots: Any, *, masked: bool = True) -> Dict[str, Any]:
    """生成聊天内确认卡数据（纯展示，不含提交动作）。"""
    form = sanitize_slots(slots)
    groups = []
    for g in FIELD_GROUPS:
        if g["key"] == "attachments":
            continue
        items = [
            {
                "key": f["key"],
                "label": f["label"],
                "value": mask_value(f["key"], form.get(f["key"], "")) if masked
                         else str(form.get(f["key"], "") or ""),
                "filled": bool(str(form.get(f["key"]) or "").strip()),
            }
            for f in FORM_FIELDS if f["group"] == g["key"]
        ]
        groups.append({"key": g["key"], "label": g["label"], "items": items})
    atts = form.get("attachments") or {}
    attachments = [
        {"key": k, "label": v, "filled": bool(atts.get(k)), "id": atts.get(k, "")}
        for k, v in ATTACHMENT_KINDS.items()
    ]
    return {"title": "入园申请信息核对", "groups": groups, "attachments": attachments}


def build_entry_payload(slots: Any, *, for_submit: bool = False,
                        note: str = "") -> Dict[str, Any]:
    """AI 工具统一的结构化返回体，带 __entry_payload__ 标记供 generate_node 提取。"""
    form = sanitize_slots(slots)
    check = validate(form, for_submit=for_submit)
    payload: Dict[str, Any] = {
        "__entry_payload__": True,
        "slots": form,
        "missing": check["missing"],
        "errors": check["errors"],
        "complete": check["complete"],
        "card": build_card(form) if check["complete"] else None,
    }
    if note:
        payload["note"] = note
    if check["complete"]:
        payload["hint"] = (
            "信息已齐备，请告知司机核对确认卡后点击「确认提交」或「去表单细改」；"
            "AI 不会代司机提交，也不会写入数据库。"
        )
    else:
        first = (check["missing"] + check["errors"])[:4]
        payload["hint"] = (
            "还缺：" + "、".join(first) + "。请用自然的口语一次性向司机询问这几项，"
            "不要逐条追问，也不要编造任何字段值。"
        )
    return payload


def llm_slots_schema() -> Dict[str, Any]:
    """给 LLM 的字段 JSON Schema（工具参数与解析 prompt 共用）。"""
    props: Dict[str, Any] = {
        f["key"]: {
            "type": "string",
            "description": f["label"] + (f"（{f['placeholder']}）" if f.get("placeholder") else ""),
        }
        for f in FORM_FIELDS
    }
    return {
        "type": "object",
        "properties": props,
        "additionalProperties": False,
    }


def describe_fields_for_prompt() -> str:
    """解析 prompt 里的字段说明，避免每次手写。"""
    lines = [f"- {f['key']}（{f['label']}）" for f in FORM_FIELDS]
    return "\n".join(lines)


def to_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# 规则抽取用的正则（LLM 不可用时兜底，保证链路可离线联调）
_RE_PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_RE_IDNO = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
_RE_PLATE = re.compile(r"([京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领][A-Z][A-Z0-9]{4,6})")
_RE_DATETIME = re.compile(r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?)")
_RE_WEIGHT = re.compile(r"(\d+(?:\.\d+)?)\s*吨")


def extract_by_rules(text: str) -> Dict[str, Any]:
    """离线兜底：从司机原文里抽高置信字段。仅在 LLM 不可用或返回失败时使用。"""
    out: Dict[str, Any] = {}
    if not text:
        return out
    m = _RE_PHONE.search(text)
    if m:
        out["phone"] = m.group(1)
    m = _RE_IDNO.search(text)
    if m:
        out["id_no"] = m.group(1).upper()
    m = _RE_PLATE.search(text)
    if m:
        out["plate_no"] = m.group(1)
    m = _RE_WEIGHT.search(text)
    if m:
        out["weight_ton"] = m.group(1)
    times = _RE_DATETIME.findall(text)
    parsed_times = [parse_dt(t) for t in times]
    parsed_times = [t for t in parsed_times if t]
    if parsed_times:
        ordered = sorted(set(parsed_times))
        out["entry_time"] = ordered[0].strftime("%Y-%m-%d %H:%M")
        if len(ordered) > 1:
            out["exit_time"] = ordered[-1].strftime("%Y-%m-%d %H:%M")
    m = re.search(r"(?:我叫|司机是|姓名[:：]?\s*)([\u4e00-\u9fa5]{2,4})(?![\u4e00-\u9fa5])", text)
    if m:
        out["driver_name"] = m.group(1)
    if "女" in text:
        out["gender"] = "女"
    elif "男" in text:
        out["gender"] = "男"
    m = re.search(r"([\u4e00-\u9fa5]{2,12}(?:有限公司|公司|化工厂|集团))", text)
    if m:
        out["company_name"] = m.group(1)
    return out


# 这些字段是「原文里逐字出现」的强格式值，模型不得改写或凭空补全。
_HIGH_CONFIDENCE_PATTERNS = {
    "phone": _RE_PHONE,
    "id_no": _RE_IDNO,
    "plate_no": _RE_PLATE,
}


def reconcile_high_confidence(incoming: Dict[str, Any],
                              rule_based: Dict[str, Any],
                              text: str) -> Dict[str, Any]:
    """校正模型给出的高置信字段：必须是司机原文里独立（带数字边界）出现过的值。

    典型故障：身份证号 110101199001011234 中间的 11 位 19900101123 会被
    缺少边界的正则/模型误当成手机号。这类值不是原文里独立出现的号码，
    因此不采信模型值，改用规则抽取到的号码；若原文根本没有合法号码，
    则删掉该字段让它回到「缺失」状态，而不是把假号码带进申请单。
    只影响 phone / id_no / plate_no，不改变文本类字段的合并策略。
    """
    out = dict(incoming)
    if not text:
        return out
    for key, pattern in _HIGH_CONFIDENCE_PATTERNS.items():
        given = str(out.get(key) or "").strip()
        if not given:
            continue
        matches = {str(m).upper() for m in pattern.findall(text)}
        if given.upper() in matches:
            continue
        if rule_based.get(key):
            out[key] = rule_based[key]
        else:
            out.pop(key, None)
    return out
