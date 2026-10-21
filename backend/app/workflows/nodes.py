"""
Workflow 通用节点实现

每个节点类型对应一个 handler，签名统一为：
    async def handler(state: WorkflowState) -> Dict[str, Any]

返回的是 state 的**局部更新**（只写要改的键），由 engine 负责合并。
只有 side_effect / notify 需要 Python 实现，注册在 HANDLERS 扩展表里。
"""

import calendar
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from .state import ConditionDef, RuleDef, StepDef, WorkflowState

logger = logging.getLogger(__name__)

# 扩展 handler 表：side_effect / notify 由业务侧注册进来
HANDLERS: Dict[str, Callable] = {}


def register_handler(name: str, fn: Callable) -> None:
    """注册一个 Python handler（side_effect / notify 用）"""
    HANDLERS[name] = fn


def get_handler(name: str) -> Optional[Callable]:
    return HANDLERS.get(name)


# ── 条件/规则求值 ──

def _get_field(state: WorkflowState, field: str) -> Any:
    """按路径取值：先看 slots，再看 step_results，再看 state 顶层"""
    if field in state.get("slots", {}):
        return state["slots"][field]
    if field in state.get("step_results", {}):
        return state["step_results"][field]
    return state.get(field)


def eval_condition(cond: Optional[ConditionDef], state: WorkflowState) -> bool:
    """求值单个条件"""
    if cond is None:
        return True
    actual = _get_field(state, cond.field)
    expected = cond.value
    op = cond.op
    try:
        if op == "gt":
            return actual is not None and actual > expected
        if op == "gte":
            return actual is not None and actual >= expected
        if op == "lt":
            return actual is not None and actual < expected
        if op == "lte":
            return actual is not None and actual <= expected
        if op == "eq":
            return actual == expected
        if op == "ne":
            return actual != expected
        if op == "in":
            return actual in (expected or [])
        if op == "not_in":
            return actual not in (expected or [])
        if op == "contains":
            return expected is not None and expected in (actual or "")
        if op == "truthy":
            return bool(actual)
        if op == "falsy":
            return not actual
    except Exception as e:
        logger.warning(f"条件求值失败 {cond.field} {cond.op} {cond.value}: {e}")
        return False
    return False


def eval_rules(rules: List[RuleDef], state: WorkflowState) -> List[str]:
    """校验规则，返回错误信息列表（空列表 = 全部通过）"""
    errors: List[str] = []
    for rule in rules:
        actual = _get_field(state, rule.field)
        expected = rule.value
        try:
            ok = False
            if rule.op == "gt":
                ok = actual is not None and actual > expected
            elif rule.op == "gte":
                ok = actual is not None and actual >= expected
            elif rule.op == "lt":
                ok = actual is not None and actual < expected
            elif rule.op == "lte":
                ok = actual is not None and actual <= expected
            elif rule.op == "eq":
                ok = actual == expected
            elif rule.op == "ne":
                ok = actual != expected
            elif rule.op == "in":
                ok = actual in (expected or [])
            elif rule.op == "not_in":
                ok = actual not in (expected or [])
            elif rule.op == "contains":
                ok = expected is not None and expected in (actual or "")
            if not ok:
                errors.append(rule.error or f"{rule.field} 不满足 {rule.op} {expected}")
        except Exception as e:
            errors.append(f"{rule.field} 校验异常: {e}")
    return errors


# ── 槽位类型转换 ──

def _shift_month(base: date, months: int) -> tuple:
    """把基准日期往后推 N 个月，返回 (年, 月)"""
    total = base.month - 1 + months
    return base.year + total // 12, total % 12 + 1


def normalize_date(raw: Any, today: Optional[date] = None) -> Any:
    """把中文相对日期归一化为 YYYY-MM-DD

    「11.30号」「11月30日」按"当年最接近的该日期"处理：若已过则取明年。
    无法解析时返回原值，交由 rule_check 报错。
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()

    base = today or datetime.now().date()
    text = str(raw).strip()

    # 已是标准格式
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", text):
        y, m, d = (int(x) for x in text.split("/"))
        return date(y, m, d).isoformat()

    # 「下个月底」/「本月底」等先处理
    if "下下个月底" in text:
        y, m = _shift_month(base, 2)
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    if "下个月底" in text or "下月月底" in text:
        y, m = _shift_month(base, 1)
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()
    if "月底" in text:
        y, m = _shift_month(base, 1 if "下月" in text else 0)
        return date(y, m, calendar.monthrange(y, m)[1]).isoformat()

    # 「X月Y日」「X.Y」「X-Y」「X/X」，含「下个月15号」这类带月份限定的写法
    offset_months = 0
    if "下下个月" in text:          # 必须先判，否则「下下个月」会被「下个月」命中
        offset_months = 2
    elif "下个月" in text or "下月" in text:
        offset_months = 1
    elif "这个月" in text or "本月" in text or "当月" in text:
        offset_months = 0

    # 「今天」「明天」「后天」
    rel = {"今天": 0, "今日": 0, "明天": 1, "明日": 1, "后天": 2, "大后天": 3}
    for word, delta in rel.items():
        if word in text:
            return (base + timedelta(days=delta)).isoformat()

    # 年月由「下个月/本月」决定，日号单独匹配：「下个月15号」
    month: Optional[int] = None
    day: Optional[int] = None

    m = re.search(r"(\d{1,2})\s*[.月/\-]\s*(\d{1,2})", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
    elif offset_months:
        m = re.search(r"(?<!\d)(\d{1,2})\s*[号日]", text)
        if m:
            month, day = base.month, int(m.group(1))

    if month is None or day is None:
        return raw

    year = base.year
    if offset_months:
        year, month = _shift_month(base, offset_months)

    try:
        candidate = date(year, month, day)
    except ValueError:
        return raw

    # 无月份限定且已过去，视为明年
    if not offset_months and candidate < base:
        try:
            candidate = date(year + 1, month, day)
        except ValueError:
            return raw
    return candidate.isoformat()


def cast_value(raw: Any, target_type: str) -> Any:
    """按槽位声明类型转换；转换失败返回原值（由校验环节报错）"""
    if raw is None:
        return None
    if target_type == "date":
        return normalize_date(raw)
    try:
        if target_type == "int":
            return int(float(raw))
        if target_type == "float":
            return float(raw)
        if target_type == "bool":
            if isinstance(raw, bool):
                return raw
            return str(raw).strip().lower() in ("1", "true", "yes", "是", "y")
        if target_type == "str":
            return str(raw)
    except (TypeError, ValueError):
        return raw
    return raw


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出里抠出 JSON 对象（容忍 ```json 包裹与前后废话）"""
    if not text:
        return None
    text = text.strip()
    # 去掉 markdown 代码块
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # 直接尝试
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    # 找第一个 {...}
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return None


# ── 通用节点 handler ──

async def slot_extract_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """抽取槽位

    优先用 user_input + 已有 slots；LLM 返回 JSON 合并进 slots。
    必填槽位缺失时不报错，而是写入 pending_question 让上层追问。
    """
    flow = deps.get("flow")
    llm = deps.get("llm_client")
    slots = dict(state.get("slots", {}) or {})
    slot_defs = {s.name: s for s in (flow.slots if flow else [])}
    required = [s for s in (flow.slots if flow else []) if s.required]

    # 1) LLM 抽取
    extracted: Dict[str, Any] = {}
    if llm is not None:
        slot_desc = "\n".join(
            f"- {s.name} ({s.type}{', 必填' if s.required else ''}): {s.description}"
            for s in (flow.slots if flow else [])
        )
        current_desc = json.dumps(slots, ensure_ascii=False)
        prompt = (
            f"{step.prompt or '从用户输入中抽取结构化字段。'}\n\n"
            f"用户输入：{state.get('user_input', '')}\n"
            f"已抽取字段：{current_desc}\n\n"
            f"可用字段定义：\n{slot_desc}\n\n"
            f"只输出 JSON 对象，key 为字段名，value 为抽取到的值。没抽到的字段不要出现。"
        )
        try:
            resp = await llm.generate(prompt, system_prompt="你是信息抽取助手，只输出 JSON，不要任何解释。")
            text = resp.get("content") or resp.get("text") or ""
            parsed = _extract_json(text)
            if parsed:
                extracted = parsed
        except Exception as e:
            logger.warning(f"槽位抽取 LLM 调用失败: {e}")

    # 2) 合并 + 类型转换
    for key, raw in extracted.items():
        if key in slot_defs:
            slots[key] = cast_value(raw, slot_defs[key].type)
        else:
            slots[key] = raw

    # 3) 检查必填缺失
    missing = [s.name for s in required if slots.get(s.name) in (None, "")]
    updates: Dict[str, Any] = {"slots": slots}
    if missing:
        names = "、".join(missing)
        question = f"还需要补充以下信息：{names}。"
        updates["pending_question"] = question
        updates["status"] = "awaiting_input"
    else:
        updates["pending_question"] = None

    # 4) 记录到 step_results，供后续步骤引用
    step_results = dict(state.get("step_results", {}) or {})
    step_results[step.id] = {"extracted": extracted, "missing": missing}
    updates["step_results"] = step_results
    return updates


async def rule_check_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """规则校验：失败则跳到 on_fail 指定步骤（或直接终止）

    失败时必定写入 status/error —— 后续 end 节点靠 status 区分
    「正常完成」与「校验不通过被终止」。
    """
    errors = eval_rules(step.rules, state)
    step_results = dict(state.get("step_results", {}) or {})
    step_results[step.id] = {"passed": not errors, "errors": errors}

    updates: Dict[str, Any] = {"step_results": step_results}
    if errors:
        message = "；".join(errors)
        updates["status"] = "failed"
        updates["error"] = message
        if step.on_fail:
            # 跳到失败处理分支（如 reject），由该分支的 end 节点收尾
            updates["goto"] = step.on_fail
    return updates


async def branch_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """条件跳转"""
    ok = eval_condition(step.condition, state)
    target = None
    if step.condition:
        target = step.condition.on_true if ok else step.condition.on_false
    step_results = dict(state.get("step_results", {}) or {})
    step_results[step.id] = {"result": ok, "target": target}
    updates: Dict[str, Any] = {"step_results": step_results}
    if target:
        updates["goto"] = target
    return updates


async def llm_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """自由生成"""
    llm = deps.get("llm_client")
    if llm is None:
        return {"error": "LLM 未初始化", "status": "failed"}

    slots = state.get("slots", {}) or {}
    step_results = state.get("step_results", {}) or {}
    prompt = (
        f"{step.prompt or '基于已知信息生成内容。'}\n\n"
        f"流程槽位：{json.dumps(slots, ensure_ascii=False)}\n"
        f"已有步骤结果：{json.dumps(step_results, ensure_ascii=False)}"
    )
    try:
        resp = await llm.generate(prompt, system_prompt=step.system_prompt or None)
        text = resp.get("content") or resp.get("text") or ""
    except Exception as e:
        return {"error": f"LLM 调用失败: {e}", "status": "failed"}

    key = step.output_key or step.id
    new_results = dict(step_results)
    new_results[key] = text
    return {"step_results": new_results}


async def side_effect_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """副作用：写库/调业务系统（Python handler）"""
    handler = get_handler(step.handler)
    if handler is None:
        return {"error": f"未注册的 handler: {step.handler}", "status": "failed"}
    try:
        result = await handler(state, step)
    except Exception as e:
        logger.exception(f"side_effect 执行失败 {step.handler}")
        return {"error": f"{step.handler} 执行失败: {e}", "status": "failed"}
    new_results = dict(state.get("step_results", {}) or {})
    new_results[step.id] = result
    return {"step_results": new_results}


async def notify_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """通知：企微/邮件（Python handler）

    通知失败不阻断流程 —— 业务已落地，通知属于 best-effort。
    """
    handler = get_handler(step.handler)
    if handler is None:
        # 未注册通知器：记日志，不算失败
        logger.warning(f"未注册通知 handler: {step.handler}，跳过通知")
        new_results = dict(state.get("step_results", {}) or {})
        new_results[step.id] = {"skipped": True, "reason": "handler not registered"}
        return {"step_results": new_results}
    try:
        result = await handler(state, step)
    except Exception as e:
        logger.warning(f"通知发送失败 {step.handler}: {e}")
        new_results = dict(state.get("step_results", {}) or {})
        new_results[step.id] = {"sent": False, "error": str(e)}
        return {"step_results": new_results}
    new_results = dict(state.get("step_results", {}) or {})
    new_results[step.id] = result
    return {"step_results": new_results}


async def end_node(step: StepDef, state: WorkflowState, deps: Dict[str, Any]) -> Dict[str, Any]:
    """终止步骤

    若上游已置 failed / rejected，则保留该状态；否则标记为正常完成。
    """
    current = state.get("status", "")
    if current in ("failed", "rejected"):
        return {"status": current}
    return {"status": "completed"}


# 通用节点表
GENERIC_NODES: Dict[str, Callable] = {
    "slot_extract": slot_extract_node,
    "rule_check": rule_check_node,
    "branch": branch_node,
    "llm": llm_node,
    "side_effect": side_effect_node,
    "notify": notify_node,
    "end": end_node,
}
