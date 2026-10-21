"""
Workflow 执行引擎

把 YAML 流程定义编译成 LangGraph StateGraph。

四个关键设计：
1. deps 不进 state —— state 会被序列化进 checkpoint，LLM client / tracer 等对象
   不可序列化。改为编译时闭包捕获 + 运行时通过 provider 获取。
2. slot_extract 拆成两个节点 —— 抽取节点无副作用可重入，闸门节点负责
   「缺必填槽位就 interrupt 追问」。这样恢复执行时不会重复调 LLM：
   抽取节点的产出已落 checkpoint，闸门节点重放只做纯判断。
3. 动态跳转靠 goto 字段 —— 节点返回 goto 目标，条件边读取后路由。
   LangGraph 的 add_conditional_edges 只能读 state，所以用 state.goto 传递。
   下一节点会把它重置为 None，避免跳转被重复消费。
4. 挂起统一用 interrupt() —— 人工审批与槽位追问走同一套机制，
   首次执行抛 GraphInterrupt 挂起，恢复时 interrupt() 返回 resume 值。
"""

import logging
import uuid
from typing import Any, Callable, Dict, List, Optional

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

try:
    from langgraph.errors import GraphInterrupt
except ImportError:  # pragma: no cover - 兼容旧版 langgraph
    class GraphInterrupt(Exception):
        """占位：旧版 langgraph 无此异常类型"""


# 挂起/恢复是控制流，不是错误 —— 必须让这类异常穿透 except Exception
_CONTROL_FLOW_EXCEPTIONS = (GraphInterrupt, KeyboardInterrupt, SystemExit)

from .loader import FlowRegistry, get_flow_registry
from .nodes import GENERIC_NODES, eval_condition
from .state import FlowDef, StepDef, WorkflowState

logger = logging.getLogger(__name__)

# 全局 deps 提供者：由 API 层注入（与 chat.py 的 get_deps 同源）
_deps_provider: Optional[Callable[[], Dict[str, Any]]] = None


def set_deps_provider(provider: Callable[[], Dict[str, Any]]) -> None:
    """注入 deps 提供者（进程内单例，API 启动时调用一次）"""
    global _deps_provider
    _deps_provider = provider


def get_deps() -> Dict[str, Any]:
    if _deps_provider is None:
        return {}
    try:
        return _deps_provider() or {}
    except Exception as e:
        logger.warning(f"获取 workflow deps 失败: {e}")
        return {}


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# 闸门节点后缀
GATE_SUFFIX = "__gate"


class WorkflowEngine:
    """流程引擎：编译并执行 YAML 定义的流程"""

    def __init__(self, registry: Optional[FlowRegistry] = None):
        self.registry = registry or get_flow_registry()
        # "flow_id:version" -> 编译好的 graph
        self._graphs: Dict[str, Any] = {}

    # ── 编译 ──

    def _node_ids(self, flow: FlowDef) -> List[str]:
        """图中的实际节点名（slot_extract 会多出一个闸门节点）"""
        ids: List[str] = []
        for s in flow.steps:
            ids.append(s.id)
            if s.type == "slot_extract":
                ids.append(s.id + GATE_SUFFIX)
        return ids

    def _build_graph(self, flow: FlowDef):
        """把 FlowDef 编译成 StateGraph"""
        graph = StateGraph(WorkflowState)

        for step in flow.steps:
            graph.add_node(step.id, self._make_node(flow, step))
            if step.type == "slot_extract":
                graph.add_node(step.id + GATE_SUFFIX, self._make_gate_node(flow, step))

        first = flow.first_step_id()
        if not first:
            raise ValueError(f"流程 {flow.id} 没有任何步骤")
        graph.set_entry_point(first)

        # 边构建
        #
        # 线性主链 = 排除 end 类型后的步骤序列。end 类型（done/reject）只能
        # 通过显式跳转（goto / on_fail）到达，不能被线性串上去 —— 否则
        # "reject" 会被当成正常后继节点在通过校验后也执行一遍。
        main_chain = [s for s in flow.steps if s.type != "end"]
        main_ids = [s.id for s in main_chain]

        # 主结束节点：优先 "done"，否则取最后一个 end 类型；都没有则直接 END
        end_steps = [s for s in flow.steps if s.type == "end"]
        primary_end = None
        for s in end_steps:
            if s.id == "done":
                primary_end = s.id
                break
        if primary_end is None and end_steps:
            primary_end = end_steps[-1].id

        for i, step in enumerate(main_chain):
            # 线性后继；主链最后一个指向主结束节点
            nxt = main_ids[i + 1] if i + 1 < len(main_ids) else (primary_end or END)
            # 显式跳转优先于线性后继
            if step.goto:
                nxt = step.goto

            if step.type == "slot_extract":
                gate_id = step.id + GATE_SUFFIX
                # 抽取 -> 闸门
                graph.add_edge(step.id, gate_id)
                # 闸门 -> 后继；仍有缺失槽位则回到闸门再问一次。
                # 必须走边路由而非节点内循环：LangGraph 恢复时会从节点开头
                # 重跑，循环里第二次以后的 interrupt 之前写入 state 的中间
                # 值不会被持久化，导致已补的槽位丢失。
                graph.add_conditional_edges(
                    gate_id,
                    self._make_gate_router(gate_id, nxt),
                    {gate_id: gate_id, nxt: nxt},
                )
            elif step.type == "branch" or step.on_fail or step.goto:
                graph.add_conditional_edges(
                    step.id,
                    self._make_router(flow, step, main_ids, primary_end),
                    self._path_map(flow),
                )
            else:
                graph.add_edge(step.id, nxt)

        # end 类型节点：只接收入边，出口统一为 END
        for step in flow.steps:
            if step.type == "end":
                graph.add_edge(step.id, END)

        return graph

    def _path_map(self, flow: FlowDef) -> Dict[str, str]:
        """条件边的目标映射：所有节点名 + END"""
        mapping = {nid: nid for nid in self._node_ids(flow)}
        mapping[END] = END
        return mapping

    def _make_gate_router(self, gate_id: str, nxt: str) -> Callable:
        """闸门节点的条件路由：_gate_retry 为 True 时回到自身继续追问。"""

        def router(state: WorkflowState) -> str:
            return gate_id if state.get("_gate_retry") else nxt

        return router

    def _make_router(self, flow: FlowDef, step: StepDef, main_ids: List[str], primary_end: Optional[str]) -> Callable:
        """条件边路由函数：读 state.goto 决定下一步"""
        valid = set(self._node_ids(flow))

        def router(state: WorkflowState) -> str:
            target = state.get("goto")
            if target and target in valid:
                return target
            # 无显式跳转：走线性主链的下一个
            if step.id in main_ids:
                idx = main_ids.index(step.id)
                return main_ids[idx + 1] if idx + 1 < len(main_ids) else (primary_end or END)
            return END

        return router

    # ── 节点构造 ──

    def _make_node(self, flow: FlowDef, step: StepDef):
        """构造单个步骤的节点函数"""

        async def node(state: WorkflowState) -> Dict[str, Any]:
            deps = get_deps()
            ctx = dict(deps)
            ctx["flow"] = flow

            tracer = deps.get("tracer")
            span_ctx = None
            if tracer:
                try:
                    from ..tracing.tracer import SpanType
                    span_ctx = tracer.start_span(
                        SpanType.WORKFLOW_STEP,
                        step.display_name(),
                        inputs={"flow_id": flow.id, "step_id": step.id, "step_type": step.type},
                    )
                except Exception:
                    span_ctx = None

            try:
                if span_ctx is not None:
                    with span_ctx as span:
                        updates = await self._execute(flow, step, state, ctx)
                        span.outputs = {
                            "step_id": step.id,
                            "status": updates.get("status", ""),
                            "goto": updates.get("goto", ""),
                            "error": updates.get("error", ""),
                        }
                        if updates.get("error"):
                            span.status = "error"
                            span.error_message = updates["error"]
                else:
                    updates = await self._execute(flow, step, state, ctx)
            except _CONTROL_FLOW_EXCEPTIONS:
                # interrupt 挂起是控制流，必须原样抛出让 LangGraph 挂起图
                raise
            except Exception as e:
                logger.exception(f"流程步骤执行失败 {flow.id}.{step.id}")
                return {
                    "current_step": step.id,
                    "goto": None,
                    "status": "failed",
                    "error": str(e),
                }

            # 统一补充执行位置；goto 由具体 handler 决定
            updates.setdefault("current_step", step.id)
            updates.setdefault("goto", None)
            updates.setdefault("status", "running")
            return updates

        return node

    def _make_gate_node(self, flow: FlowDef, step: StepDef):
        """槽位闸门节点：必填槽位缺失就 interrupt 追问

        设计要点（LangGraph interrupt 语义）：
        节点内**只调用一次** interrupt。恢复时 LangGraph 从节点开头重跑全部逻辑，
        interrupt() 返回 resume 值。若在节点内用 while 循环多次 interrupt，
        resume 值只会按顺序匹配到各次 interrupt，且循环里的中间状态不会
        写回 state —— 用户第一次补的槽位会丢失。

        所以这里只负责「问一次 + 合并一次」，"还缺什么" 由边路由判断：
        缺则回到本节点再问一次，不缺则走向后继。每次重入都会把已补的
        槽位从 state 带过来，因此多轮补充不会丢数据。

        与抽取节点分离的原因：闸门重放时不会重复调用 LLM 抽取。
        """

        async def gate(state: WorkflowState) -> Dict[str, Any]:
            slots = dict(state.get("slots", {}) or {})
            required = [s for s in flow.slots if s.required]
            missing = [s for s in required if slots.get(s.name) in (None, "")]

            # 已齐备：直接放行（resume 重跑时会走到这里）
            if not missing:
                return {
                    "slots": slots,
                    "pending_question": None,
                    "status": "running",
                    "goto": None,
                    # 必须显式置 False：state 里的旧值会被保留，
                    # 若沿用上一轮的 True 会在这里原地死循环。
                    "_gate_retry": False,
                }

            names = "、".join(s.name for s in missing)
            payload = {
                "type": "slot_request",
                "step_id": step.id,
                "question": f"还需要补充以下信息：{names}。",
                "missing": [
                    {
                        "name": s.name,
                        "type": s.type,
                        "required": s.required,
                        "description": s.description,
                    }
                    for s in missing
                ],
            }

            # 首次执行挂起；恢复时返回用户补充的内容
            answer = interrupt(payload)

            extra: Dict[str, Any] = {}
            if isinstance(answer, dict):
                extra = answer.get("slots") or {}
                if not extra and answer.get("user_input"):
                    extra = {"__raw__": answer["user_input"]}
            elif isinstance(answer, str):
                extra = {"__raw__": answer}

            from .nodes import cast_value

            if "__raw__" in extra:
                # 自由文本：交给 LLM 再抽一次
                raw = extra["__raw__"]
                extracted = await self._reextract(flow, step, raw, ctx_deps(), filled=slots)
                slot_defs = {s.name: s for s in flow.slots}
                for k, v in (extracted or {}).items():
                    # 已确认的槽位不被模型返回的旧值覆盖
                    if k in slots and slots.get(k) not in (None, ""):
                        continue
                    slots[k] = cast_value(v, slot_defs[k].type) if k in slot_defs else v
            else:
                slot_defs = {s.name: s for s in flow.slots}
                for k, v in extra.items():
                    slots[k] = cast_value(v, slot_defs[k].type) if k in slot_defs else v

            # 合并后仍缺失时，由边路由把本节点再拉回来问一次
            still_missing = [s.name for s in required if slots.get(s.name) in (None, "")]

            return {
                "slots": slots,
                "pending_question": f"还需要补充以下信息：{'、'.join(still_missing)}。" if still_missing else None,
                "status": "running",
                "goto": None,
                "_gate_retry": bool(still_missing),
            }

        return gate

    async def _reextract(
        self,
        flow: FlowDef,
        step: StepDef,
        raw_text: str,
        deps: Dict[str, Any],
        filled: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """闸门里对用户自由文本再抽一次槽位

        filled 是已经抽到的槽位：既用于排除已确认的字段，也用于在 prompt 里
        显式告知模型，避免它重复返回旧值把用户已确认的内容覆盖掉。
        """
        llm = deps.get("llm_client")
        if llm is None:
            return {}

        slot_desc = "\n".join(
            f"- {s.name} ({s.type}{', 必填' if s.required else ''}): {s.description}"
            for s in flow.slots
        )
        filled_hint = ""
        if filled:
            pairs = "、".join(f"{k}={v}" for k, v in filled.items())
            remaining = [s.name for s in flow.slots if s.name not in filled]
            filled_hint = (
                f"\n已确认的字段（不要重复输出，也不要修改其值）：{pairs}\n"
                f"本次只需抽取：{'、'.join(remaining) if remaining else '（无）'}\n"
            )
        prompt = (
            f"{step.prompt or '从用户输入中抽取结构化字段。'}\n\n"
            f"用户输入：{raw_text}\n\n"
            f"可用字段定义：\n{slot_desc}\n"
            f"{filled_hint}\n"
            f"只输出 JSON 对象，key 为字段名，value 为抽取到的值。没抽到的字段不要出现。"
        )
        try:
            resp = await llm.generate(prompt, system_prompt="你是信息抽取助手，只输出 JSON，不要任何解释。")
            text = resp.get("content") or resp.get("text") or ""
            from .nodes import _extract_json
            return _extract_json(text) or {}
        except Exception as e:
            logger.warning(f"闸门重抽取失败: {e}")
            return {}

    async def _execute(self, flow: FlowDef, step: StepDef, state: WorkflowState, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """执行单个步骤：条件判断 -> 分派到 handler"""
        # when 条件不满足：跳过本步
        if step.when and not eval_condition(step.when, state):
            logger.info(f"跳过步骤 {step.id}（when 条件不满足）")
            step_results = dict(state.get("step_results", {}) or {})
            step_results[step.id] = {"skipped": True, "reason": "when 条件不满足"}
            return {"step_results": step_results}

        # human_approval 由引擎直接处理（需要 interrupt 挂起语义），
        # 不在通用节点表里
        if step.type == "human_approval":
            return await self._approval_node(flow, step, state, ctx)

        handler = GENERIC_NODES.get(step.type)
        if handler is None:
            return {"error": f"未知节点类型: {step.type}", "status": "failed"}

        return await handler(step, state, ctx)

    async def _approval_node(self, flow: FlowDef, step: StepDef, state: WorkflowState, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """人工卡点：interrupt 挂起，恢复时读取审批结果"""
        approver_role = step.approver_role or "manager"
        payload = {
            "type": "approval_request",
            "step_id": step.id,
            "step_name": step.display_name(),
            "approver_role": approver_role,
            "hint": step.approver_hint,
            "slots": state.get("slots", {}),
        }

        decision = interrupt(payload)

        approved = False
        comment = ""
        if isinstance(decision, dict):
            approved = bool(decision.get("approved"))
            comment = str(decision.get("comment", "") or "")
        elif isinstance(decision, bool):
            approved = decision

        step_results = dict(state.get("step_results", {}) or {})
        step_results[step.id] = {"approved": approved, "comment": comment}

        updates: Dict[str, Any] = {
            "step_results": step_results,
            "pending_approval": None,
        }
        if not approved:
            # 驳回：走 on_fail（若配置）或直接结束
            updates["status"] = "rejected"
            updates["error"] = comment or "审批被驳回"
            if step.on_fail:
                updates["goto"] = step.on_fail
        return updates

    # ── 对外接口 ──

    def get_graph(self, flow_id: str, version: Optional[int] = None, checkpointer: Any = None):
        """获取（并缓存）编译好的 graph

        checkpointer 只在首次编译时生效 —— graph 一旦编译，checkpoint 绑定
        就固定了。切换 checkpointer 需要 invalidate。
        """
        flow = self.registry.get(flow_id, version)
        if flow is None:
            return None
        cache_key = f"{flow.id}:{flow.version}"
        if cache_key not in self._graphs:
            graph = self._build_graph(flow)
            if checkpointer is not None:
                graph = graph.compile(checkpointer=checkpointer)
            else:
                graph = graph.compile()
            self._graphs[cache_key] = graph
            logger.info(f"流程图编译完成: {cache_key}")
        return self._graphs[cache_key]

    def invalidate(self, flow_id: Optional[str] = None) -> None:
        """清除缓存（流程定义热更新或切换 checkpointer 时调用）"""
        if flow_id is None:
            self._graphs.clear()
        else:
            for key in list(self._graphs.keys()):
                if key.split(":")[0] == flow_id:
                    del self._graphs[key]


def ctx_deps() -> Dict[str, Any]:
    """闸门节点内取 deps 的便捷函数"""
    return get_deps()


# ── 全局引擎单例 ──
_engine: Optional[WorkflowEngine] = None


def get_workflow_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
