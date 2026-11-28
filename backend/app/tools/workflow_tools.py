"""
下单工具 —— 让 LLM 识别出「下单意图」后启动 workflow

设计要点：
1. 只在**明确下单**时触发。查库存、查订单状态、问价格这类只读请求
   不应该走这里，靠工具描述约束 + 槽位完整度后置校验双重把关。
2. 启动流程走的是 workflow 引擎（有checkpoint、能追问、能挂起），
   不是一次性的函数调用 —— 所以这里返回的可能是「还需补充信息」。
3. 引擎侧的 LLM 客户端与RAG 侧共用单例，不额外占用资源。
"""

import hashlib
import json
import logging
import re
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 明确是只读查询的说法，命中则拒绝启动流程。
# 这是防误触发的**兜底**——主要靠工具描述引导 LLM 判断，
# 这里是第二道闸：万一 LLM 仍然误调了，不至于真去建单。
_READ_ONLY_HINTS = [
    "查一下", "查查", "查询", "看看", "查下", "多少", "有没有货", "库存",
    "什么状态", "到哪了", "进度", "价格", "多少钱", "报价", "是什么",
]


def _looks_like_read_only(user_input: str) -> bool:
    text = user_input or ""
    return any(h in text for h in _READ_ONLY_HINTS)


_PUNCT_RE = re.compile(r"[\s,，。.!！?？:：;；'\"`~·\-_/\\()（）\[\]【】]+")


def _business_idempotency_key(flow_id: str, user_input: str, username: str = "") -> str:
    """计算稳定的业务幂等键。

    run_id 每次调用都新建（流程实例唯一，用于 resume），不能作幂等键——
    工具循环重试或用户重复提交会生成不同 run_id，导致 ERP 重复建单。
    这里用「用户 + 流程 + 规范化原文」的哈希：同一业务意图跨请求得到同一个键，
    由 handler 写入 ERP 的「幂等键」列做去重；不同用户即使下同样的单也不互相影响。

    身份取自请求入口绑定的 contextvar，不接受 LLM 传参，避免伪造。
    原文只留哈希、不落日志，避免 PII 泄露。
    """
    normalized = _PUNCT_RE.sub("", user_input or "").strip()
    seed = f"{username}|{flow_id}|{normalized}".encode("utf-8")
    return hashlib.sha256(seed).hexdigest()[:20]


async def tool_create_order(
    user_input: str,
    flow_id: str = "methane_order",
    version: Any = None,
) -> str:
    """启动下单流程。user_input: 用户的原始下单请求原文。"""
    if not user_input or not user_input.strip():
        return json.dumps({"error": "user_input 不能为空"}, ensure_ascii=False)

    # 后置校验一：只读请求直接拒绝，避免建错单
    if _looks_like_read_only(user_input):
        logger.info(f"[create_order] 疑似只读查询，不启动流程: {user_input[:40]}")
        return json.dumps(
            {"started": False, "reason": "这是查询类请求，不需要走下单流程"},
            ensure_ascii=False,
        )

    # P0 角色闸门：身份来自请求入口 contextvar，不接受 LLM 传参。
    # 防止提示注入（文档/用户问题里藏指令）诱导模型越权下单。
    try:
        from ..flows.context import role_id as _current_role
        from ..core.config import get_settings
        role = _current_role() or ""
        allowed = set(get_settings().ORDER_TOOL_ALLOWED_ROLES or [])
        if role != "admin" and role not in allowed:
            logger.warning(f"[create_order] 角色 {role!r} 无下单权限，已拒绝")
            return json.dumps(
                {"started": False, "reason": "当前角色无权下单，请知悉管理员"},
                ensure_ascii=False,
            )
    except Exception as e:
        # 配置/上下文异常时 fail-closed：宁可拒绝，也不放行未知身份下单
        logger.error(f"[create_order] 角色校验失败，拒绝下单: {e}")
        return json.dumps(
            {"started": False, "reason": "下单权限校验不可用，请稍后重试"},
            ensure_ascii=False,
        )

    try:
        from ..workflows.loader import get_flow_registry
        from ..workflows.engine import get_workflow_engine, new_run_id
        from ..workflows.checkpointer import get_checkpointer
    except Exception as e:
        logger.error(f"[create_order] workflow 模块不可用: {e}")
        return json.dumps({"error": f"流程引擎不可用: {e}"}, ensure_ascii=False)

    try:
        registry = get_flow_registry()
        flow = registry.get(flow_id, version)
        if flow is None:
            return json.dumps({"error": f"流程不存在: {flow_id}"}, ensure_ascii=False)

        checkpointer = await get_checkpointer()
        graph = get_workflow_engine().get_graph(
            flow.id, flow.version, checkpointer=checkpointer
        )
        if graph is None:
            return json.dumps({"error": f"流程图编译失败: {flow_id}"}, ensure_ascii=False)

        # 身份从请求入口绑定的 contextvar 取，不接受 LLM 传参（防伪造）
        try:
            from ..flows.context import username as _current_username
            username = _current_username() or ""
        except Exception:
            username = ""
        idem_key = _business_idempotency_key(flow.id, user_input, username)

        run_id = new_run_id()
        config = {"configurable": {"thread_id": run_id}}
        initial = {
            "run_id": run_id,
            "flow_id": flow.id,
            "flow_version": flow.version,
            "slots": {},
            "step_results": {},
            "current_step": flow.steps[0].id,
            "goto": None,
            "pending_approval": None,
            "status": "running",
            "error": None,
            # P0: 稳定业务幂等键写进 state，handler 写库时优先用它而非 run_id
            "metadata": {"source": "create_order_tool", "idempotency_key": idem_key},
            "user_input": user_input.strip(),
            "pending_question": None,
        }

        result = await graph.ainvoke(initial, config=config)
        # interrupt 载荷不在 ainvoke 返回值里，要从快照的 pending_writes 取
        snap = await graph.aget_state(config)
        from ..workflows.checkpointer import extract_interrupt_payloads
        interrupts = extract_interrupt_payloads(snap)
    except Exception as e:
        logger.exception(f"[create_order] 启动流程失败 {flow_id}")
        return json.dumps({"error": f"下单流程启动失败: {e}"}, ensure_ascii=False)

    return _render_result(result, run_id, flow_id, interrupts)


async def tool_resume_order(
    run_id: str,
    user_input: str = "",
    approved: Any = None,
    slots: Any = None,
) -> str:
    """恢复一个挂起的下单流程。

    用户在对话里补充了信息（比如"客户是X化工厂"）时调用。
    run_id 从上一次 create_order 的返回值里取。
    """
    if not run_id or not run_id.strip():
        return json.dumps({"error": "run_id 不能为空"}, ensure_ascii=False)

    try:
        from ..workflows.loader import get_flow_registry
        from ..workflows.engine import get_workflow_engine
        from ..workflows.checkpointer import get_checkpointer, extract_interrupt_payloads
        from langgraph.types import Command
    except Exception as e:
        logger.error(f"[resume_order] workflow 模块不可用: {e}")
        return json.dumps({"error": f"流程引擎不可用: {e}"}, ensure_ascii=False)

    payload: Dict[str, Any] = {}
    if slots:
        payload["slots"] = slots
    if user_input:
        payload["user_input"] = user_input
    if approved is not None:
        payload["approved"] = approved
    if not payload:
        return json.dumps({"error": "需要提供 user_input 或 slots 或 approved"}, ensure_ascii=False)

    try:
        checkpointer = await get_checkpointer()
        engine = get_workflow_engine()
        registry = get_flow_registry()
        config = {"configurable": {"thread_id": run_id}}

        # 从 checkpoint 反查这个 run 属于哪个 flow
        graph = None
        flow_id = ""
        owner = None
        for meta in registry.list_flows():
            candidate = registry.get(meta["id"], meta["version"])
            if candidate is None:
                continue
            g = engine.get_graph(candidate.id, candidate.version, checkpointer=checkpointer)
            if g is None:
                continue
            snap = await g.aget_state(config)
            values = getattr(snap, "values", None) or {}
            if values.get("run_id") == run_id:
                graph = g
                flow_id = candidate.id
                owner = (values.get("metadata") or {}).get("username")
                break

        if graph is None:
            return json.dumps({"error": f"流程实例不存在或已过期: {run_id}"}, ensure_ascii=False)

        # P0: 仅发起人本人可续单；身份来自 contextvar，LLM 无法伪造
        try:
            from ..flows.context import username as _current_username, role_id as _current_role
            me, my_role = _current_username() or "", _current_role() or ""
        except Exception:
            me, my_role = "", ""
        if owner and my_role != "admin" and owner != me:
            logger.warning(f"[resume_order] 拒绝越权续单: run_id={run_id} owner={owner} me={me}")
            return json.dumps({"error": "无权恢复该流程实例"}, ensure_ascii=False)

        result = await graph.ainvoke(Command(resume=payload), config=config)
        snap = await graph.aget_state(config)
        interrupts = extract_interrupt_payloads(snap)
    except Exception as e:
        logger.exception(f"[resume_order] 恢复流程失败 run_id={run_id}")
        return json.dumps({"error": f"恢复失败: {e}"}, ensure_ascii=False)

    return _render_result(result, run_id, flow_id, interrupts)


def _render_result(
    result: Dict[str, Any],
    run_id: str,
    flow_id: str,
    interrupts: list = None,
) -> str:
    """把 workflow 结果整理成给 LLM 看的结构"""
    slots = result.get("slots", {}) or {}
    step_results = result.get("step_results", {}) or {}
    interrupts = interrupts or []
    status = result.get("status", "")

    payload: Dict[str, Any] = {
        "run_id": run_id,
        "flow_id": flow_id,
        "status": status,
        "extracted": slots,
    }

    if status == "completed":
        write_res = step_results.get("write_erp") or step_results.get("write_pending_order") or {}
        payload["order_no"] = write_res.get("order_no")
        payload["message"] = (
            f"下单成功，单号 {write_res.get('order_no')}，"
            f"已进入待排产池，等待排产。"
        )
        return json.dumps(payload, ensure_ascii=False)

    if status == "failed":
        payload["message"] = f"下单未通过校验：{result.get('error', '原因未知')}"
        return json.dumps(payload, ensure_ascii=False)

    # 挂起中：需要追问用户
    question = result.get("pending_question")
    if not question and interrupts:
        question = interrupts[0].get("question") if isinstance(interrupts[0], dict) else None
    payload["need_more_info"] = True
    payload["question"] = question or "请补充订单信息"
    payload["next_action"] = (
        f"用 resume_order 工具继续，run_id={run_id}，"
        f"把用户补充的内容放进 user_input"
    )
    payload["message"] = (
        "订单信息还不完整，需要向用户追问。"
        f"请用自然语言提出这个问题：{payload['question']}"
    )
    return json.dumps(payload, ensure_ascii=False)


# ── 工具 schema（注册到 LLM 的 function calling） ──

CREATE_ORDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_order",
        "description": (
            "提交产品订单到ERP 待排产池。当用户明确表达下单、订货、采购、"
            "要货、发货等意图时使用，例如「下一个5吨甲烷订单」「帮我订20吨乙烯」。"
            "注意：查询订单状态、查库存、问价格、问交期等只读请求不要使用本工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user_input": {
                    "type": "string",
                    "description": "用户的原始下单请求原文，完整传入，不要改写",
                },
                "flow_id": {
                    "type": "string",
                    "description": "流程ID。化工产品下单填 methane_order",
                    "default": "methane_order",
                },
            },
            "required": ["user_input"],
        },
    },
}

RESUME_ORDER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "resume_order",
        "description": (
            "继续一个挂起的下单流程。当系统提示订单信息不完整、需要补充"
            "（如缺客户、交期、数量）时，用上次 create_order 返回的 run_id "
            "调用本工具，把用户刚补充的内容传进来。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "待排产订单的 run_id，来自 create_order 的返回值",
                },
                "user_input": {
                    "type": "string",
                    "description": "用户补充的原始信息原文，如『客户是X化工厂』",
                },
                "slots": {
                    "type": "object",
                    "description": "结构化的槽位补充，如 {\"customer\": \"X化工厂\"}",
                    "additionalProperties": True,
                },
                "approved": {
                    "type": "boolean",
                    "description": "审批场景传true 表示通过，false 表示驳回",
                },
            },
            "required": ["run_id"],
        },
    },
}