"""
甲烷下单流程 + Excel 模拟 ERP 的测试

覆盖：
- 中文相对日期归一化（11.30号 / 下个月底 / 下下个月15号 / 明天）
- ERP 待排产池写入、单号递增、幂等去重
- 夜间排产批处理（按交期排序、回写状态、生成计划）
- workflow 端到端（追问 → 补齐 → 写 ERP）
- create_order 工具的只读请求拦截
"""

import asyncio
import json
import os
import tempfile
from datetime import date, timedelta

import pytest
import pytest_asyncio


# ── 日期归一化 ──


@pytest.mark.parametrize("raw,expected", [
    ("11.30号", "2026-11-30"),
    ("11月30日", "2026-11-30"),
    ("11/30", "2026-11-30"),
    ("2026-11-30", "2026-11-30"),
    ("2026/11/30", "2026-11-30"),
    ("下个月底", "2026-11-30"),
    ("本月底", "2026-10-31"),
    ("下个月15号", "2026-11-15"),
    ("下月20号", "2026-11-20"),
    ("下下个月10号", "2026-12-10"),
    ("明天", "2026-10-22"),
    ("后天", "2026-10-23"),
    ("1.5号", "2027-01-05"),        # 已过日期视为明年
])
def test_normalize_date(raw, expected):
    from app.workflows.nodes import normalize_date
    assert normalize_date(raw, date(2026, 10, 21)) == expected


def test_normalize_date_passes_through_unparseable():
    """解析不了的值原样返回，由 rule_check 报错"""
    from app.workflows.nodes import normalize_date
    assert normalize_date("随便什么", date(2026, 10, 21)) == "随便什么"
    assert normalize_date("", date(2026, 10, 21)) is None
    assert normalize_date(None, date(2026, 10, 21)) is None


def test_normalize_date_crosses_year():
    from app.workflows.nodes import normalize_date
    assert normalize_date("下个月底", date(2026, 12, 15)) == "2027-01-31"


def test_cast_value_supports_date_type():
    from app.workflows.nodes import cast_value
    assert cast_value("11.30号", "date") is not None
    assert cast_value("11.30号", "date").count("-") == 2


# ── ERP 模拟层 ──


@pytest_asyncio.fixture(autouse=True)
async def _close_global_checkpointer():
    """每个用例后关闭全局 checkpointer 单例

    工具函数（tool_resume_order 等）内部调 get_checkpointer() 会创建**全局单例**，
    用例自己的 fixture 管不到。不关的话 aiosqlite 的后台线程不退出，pytest 会挂住。
    """
    yield
    from app.workflows.checkpointer import close_checkpointer
    await close_checkpointer()


@pytest.fixture
def erp_dir(monkeypatch):
    """把ERP 数据目录指到临时目录，避免污染项目 data/"""
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ERP_DATA_DIR", d)
    return d


@pytest.fixture
def erp(erp_dir):
    from app.workflows.handlers import erp as erp_mod
    return erp_mod


def _order(idem: str, product="甲烷", volume=5, due="2026-11-30", customer="X化工厂"):
    return {
        "idempotency_key": idem,
        "product": product,
        "volume_ton": volume,
        "delivery_date": due,
        "customer": customer,
        "contact": "",
        "remark": "",
        "run_id": f"run-{idem}",
    }


def test_erp_writes_all_fields(erp):
    erp.append_pending_order(_order("k1"))
    rows = erp.read_pending()
    assert len(rows) == 1
    r = rows[0]
    assert r["产品"] == "甲烷", "产品字段曾因中英键名不一致写成 None"
    assert r["数量(吨)"] == 5
    assert r["交期"] == "2026-11-30"
    assert r["客户"] == "X化工厂"
    assert r["状态"] == "pending"


def test_erp_order_no_increments(erp):
    a = erp.append_pending_order(_order("k1"))
    b = erp.append_pending_order(_order("k2"))
    c = erp.append_pending_order(_order("k3"))
    assert a["order_no"] != b["order_no"] != c["order_no"]
    assert b["order_no"] > a["order_no"]
    assert len(erp.read_pending()) == 3


def test_erp_is_idempotent(erp):
    """同一幂等键重复写入不能建重复单（节点重入会发生）"""
    first = erp.append_pending_order(_order("k1"))
    second = erp.append_pending_order(_order("k1", product="别的", volume=999))
    assert second["duplicated"] is True
    assert second["order_no"] == first["order_no"]
    assert len(erp.read_pending()) == 1
    assert erp.read_pending()[0]["产品"] == "甲烷", "幂等命中时不应覆盖原记录"


def test_erp_list_pending_sorted_by_delivery(erp):
    erp.append_pending_order(_order("k1", due="2026-12-20"))
    erp.append_pending_order(_order("k2", due="2026-11-10"))
    erp.append_pending_order(_order("k3", due="2026-12-01"))
    rows = erp.list_pending_orders()
    assert [r["交期"] for r in rows] == ["2026-11-10", "2026-12-01", "2026-12-20"]


def test_erp_mark_scheduled(erp):
    erp.append_pending_order(_order("k1"))
    no = erp.read_pending()[0]["单号"]
    changed = erp.mark_scheduled([no], "2026-10-25", "L1")
    assert changed == 1
    r = erp.read_pending()[0]
    assert r["状态"] == "scheduled"
    assert r["排产日期"] == "2026-10-25"
    assert r["产线"] == "L1"
    assert erp.list_pending_orders() == [], "已排产的不应再出现在 pending 列表"


# ── 夜间排产批处理 ──


def test_nightly_scheduling_orders_by_delivery(erp):
    from app.workflows.handlers import nightly_scheduling

    erp.append_pending_order(_order("k1", due="2026-12-20"))
    erp.append_pending_order(_order("k2", due="2026-11-05"))
    erp.append_pending_order(_order("k3", due="2026-12-01"))

    result = nightly_scheduling(line="L2")
    assert result["scheduled"] == 3

    plan = erp.read_plan()
    assert [r["交期"] for r in plan] == ["2026-11-05", "2026-12-01", "2026-12-20"]
    assert [r["排产序号"] for r in plan] == [1, 2, 3]
    assert all(r["产线"] == "L2" for r in plan)
    # 全部回写为已排产
    assert erp.list_pending_orders() == []


def test_nightly_scheduling_empty_pool(erp):
    from app.workflows.handlers import nightly_scheduling
    result = nightly_scheduling()
    assert result["scheduled"] == 0
    assert "为空" in result["message"]


def test_nightly_scheduling_urgent_first(erp):
    """交期越近排产日越早"""
    from app.workflows.handlers import nightly_scheduling

    today = date.today()
    near = (today + timedelta(days=3)).isoformat()
    far = (today + timedelta(days=60)).isoformat()
    erp.append_pending_order(_order("k1", due=near))
    erp.append_pending_order(_order("k2", due=far))

    nightly_scheduling()
    plan = {r["单号"]: r for r in erp.read_plan()}
    near_row = [r for r in plan.values() if r["交期"] == near][0]
    far_row = [r for r in plan.values() if r["交期"] == far][0]
    assert near_row["排产日期"] <= far_row["排产日期"]


def test_nightly_scheduling_not_rerun_duplicates(erp):
    """已排产的订单不该被二次排产"""
    from app.workflows.handlers import nightly_scheduling

    erp.append_pending_order(_order("k1"))
    assert nightly_scheduling()["scheduled"] == 1
    assert nightly_scheduling()["scheduled"] == 0, "第二轮不应重复排产"


# ── workflow 端到端 ──


class _OrderLLM:
    """按预设返回 JSON：首轮抽产品/数量/交期，补客户时抽客户"""

    def __init__(self):
        self.calls = 0

    async def generate(self, prompt, system_prompt=None, **kwargs):
        self.calls += 1
        if "可用字段定义" in prompt:
            if "X化工厂" in prompt:
                return {"content": '{"customer":"X化工厂"}'}
            return {"content": '{"product":"甲烷","volume_ton":5,"delivery_date":"11.30号"}'}
        return {"content": "常规订单，无需人工确认。"}


@pytest_asyncio.fixture
async def order_checkpointer(tmp_path):
    from app.workflows.checkpointer import close_checkpointer, get_checkpointer
    cp = await get_checkpointer(str(tmp_path / "order.db"))
    try:
        yield cp
    finally:
        await close_checkpointer()


def _initial(run_id):
    return {
        "run_id": run_id, "flow_id": "methane_order", "flow_version": 1,
        "slots": {}, "step_results": {}, "current_step": "collect", "goto": None,
        "pending_approval": None, "status": "running", "error": None,
        "metadata": {"username": "sales01"},
        "user_input": "帮我下一个11.30号发货的体积5吨的甲烷订单",
        "pending_question": None,
    }


def _pending_interrupts(snapshot):
    out = []
    for t in (getattr(snapshot, "tasks", None) or []):
        for i in (getattr(t, "interrupts", None) or []):
            out.append(getattr(i, "value", None))
    return out


@pytest.mark.asyncio
async def test_order_flow_end_to_end(erp_dir, order_checkpointer):
    """完整下单：抽槽位 → 追问客户 → 补齐 → 写 ERP → 完成"""
    from langgraph.types import Command

    from app.workflows.engine import WorkflowEngine, new_run_id, set_deps_provider
    from app.workflows.handlers import erp as erp_mod, register_builtin_handlers
    from app.workflows.loader import FlowRegistry

    register_builtin_handlers()
    set_deps_provider(lambda: {"llm_client": _OrderLLM()})

    registry = FlowRegistry()
    registry.load(force=True)
    assert registry.get("methane_order") is not None, "methane_order.yaml 应被加载"

    graph = WorkflowEngine(registry=registry).get_graph(
        "methane_order", 1, checkpointer=order_checkpointer
    )

    run_id = new_run_id()
    config = {"configurable": {"thread_id": run_id}}

    # 首轮：缺客户，应挂起追问
    result = await graph.ainvoke(_initial(run_id), config=config)
    assert result["slots"]["product"] == "甲烷"
    assert result["slots"]["volume_ton"] == 5.0
    assert result["slots"]["delivery_date"].count("-") == 2, "交期应已归一化"

    snapshot = await graph.aget_state(config)
    interrupts = _pending_interrupts(snapshot)
    assert interrupts, "缺客户时应挂起"
    assert "customer" in interrupts[0]["question"]

    # 二轮：补齐后写 ERP 并完成
    result = await graph.ainvoke(
        Command(resume={"user_input": "客户是X化工厂"}), config=config
    )
    assert result["slots"]["customer"] == "X化工厂"
    assert result["status"] == "completed"
    assert result["step_results"]["write_erp"]["erp_synced"] is True

    rows = erp_mod.read_pending()
    assert len(rows) == 1
    assert rows[0]["产品"] == "甲烷"
    assert rows[0]["状态"] == "pending"


@pytest.mark.asyncio
async def test_order_flow_rejects_oversized(erp_dir, order_checkpointer):
    """超过 200 吨应被 rule_check 拦下，状态 failed，不写 ERP"""
    from langgraph.types import Command

    from app.workflows.engine import WorkflowEngine, new_run_id, set_deps_provider
    from app.workflows.handlers import erp as erp_mod, register_builtin_handlers
    from app.workflows.loader import FlowRegistry

    class _BigLLM:
        async def generate(self, prompt, system_prompt=None, **kwargs):
            if "可用字段定义" in prompt:
                if "X化工厂" in prompt:
                    return {"content": '{"customer":"X化工厂"}'}
                return {"content": '{"product":"甲烷","volume_ton":500,"delivery_date":"11.30号"}'}
            return {"content": "大客户。"}

    register_builtin_handlers()
    set_deps_provider(lambda: {"llm_client": _BigLLM()})

    registry = FlowRegistry()
    registry.load(force=True)
    graph = WorkflowEngine(registry=registry).get_graph(
        "methane_order", 1, checkpointer=order_checkpointer
    )

    run_id = new_run_id()
    config = {"configurable": {"thread_id": run_id}}
    await graph.ainvoke(_initial(run_id), config=config)
    result = await graph.ainvoke(Command(resume={"slots": {"customer": "X化工厂"}}), config=config)

    assert result["status"] == "failed"
    assert "200" in (result.get("error") or "")
    assert erp_mod.read_pending() == [], "校验失败不应写 ERP"


@pytest.mark.asyncio
async def test_order_flow_not_rerun_erp_on_resume(erp_dir, order_checkpointer):
    """恢复执行时节点重入，ERP 侧靠幂等键兜底不能建重复单"""
    from langgraph.types import Command

    from app.workflows.engine import WorkflowEngine, new_run_id, set_deps_provider
    from app.workflows.handlers import erp as erp_mod, register_builtin_handlers
    from app.workflows.loader import FlowRegistry

    register_builtin_handlers()
    set_deps_provider(lambda: {"llm_client": _OrderLLM()})

    registry = FlowRegistry()
    registry.load(force=True)
    graph = WorkflowEngine(registry=registry).get_graph(
        "methane_order", 1, checkpointer=order_checkpointer
    )

    run_id = new_run_id()
    config = {"configurable": {"thread_id": run_id}}
    await graph.ainvoke(_initial(run_id), config=config)
    await graph.ainvoke(Command(resume={"user_input": "客户是X化工厂"}), config=config)

    first_no = erp_mod.read_pending()[0]["单号"]

    # 再恢复一次（模拟重复提交），幂等键应拦住
    erp_mod.append_pending_order({
        "idempotency_key": f"{run_id}:write_erp",
        "product": "甲烷", "volume_ton": 5, "delivery_date": "2026-11-30",
        "customer": "X化工厂", "contact": "", "remark": "", "run_id": run_id,
    })
    rows = erp_mod.read_pending()
    assert len(rows) == 1
    assert rows[0]["单号"] == first_no


# ── create_order 工具 ──


def test_read_only_requests_are_blocked():
    """只读请求不能启动下单流程（防误触发兜底）"""
    from app.tools.workflow_tools import _looks_like_read_only

    assert _looks_like_read_only("帮我下一个11.30号发货的体积5吨的甲烷订单") is False
    assert _looks_like_read_only("我要订20吨乙烯") is False
    assert _looks_like_read_only("查一下甲烷还有多少库存") is True
    assert _looks_like_read_only("看看订单到哪了") is True
    assert _looks_like_read_only("甲烷多少钱一吨") is True


@pytest.mark.asyncio
async def test_create_order_tool_rejects_read_only():
    from app.tools.workflow_tools import tool_create_order

    out = await tool_create_order("查一下甲烷库存")
    payload = json.loads(out)
    assert payload["started"] is False
    assert "查询" in payload["reason"]


@pytest.mark.asyncio
async def test_create_order_tool_rejects_empty():
    from app.tools.workflow_tools import tool_create_order

    payload = json.loads(await tool_create_order(""))
    assert "error" in payload


def test_create_order_tool_is_registered():
    from app.tools.agent_tools import TOOL_REGISTRY, TOOLS_SCHEMA, _HAS_WORKFLOW

    assert _HAS_WORKFLOW is True
    names = [t["function"]["name"] for t in TOOLS_SCHEMA]
    assert "create_order" in names
    assert "create_order" in TOOL_REGISTRY


def test_create_order_schema_mentions_read_only_exclusion():
    """工具描述必须明确排除只读请求，这是防误触发的主要手段"""
    from app.tools.workflow_tools import CREATE_ORDER_SCHEMA

    desc = CREATE_ORDER_SCHEMA["function"]["description"]
    assert "不要使用" in desc
    assert "只读" in desc

# ── 聊天场景：function calling 闭环 ──


class _ScriptedLLM:
    """按脚本演化的 function calling 模型，模拟真实多轮行为"""

    def __init__(self):
        self.step = 0
        self.run_id = None
        self.seen_tools = []

    async def chat_with_tools(self, messages, tools=None, **kw):
        self.step += 1
        self.seen_tools = [t["function"]["name"] for t in (tools or [])]
        tool_results = [m for m in messages if m.get("role") == "tool"]
        last = json.loads(tool_results[-1]["content"]) if tool_results else None

        if self.step == 1:
            return {
                "text": "",
                "tool_calls": [{
                    "id": "c1",
                    "function": {"name": "create_order", "arguments": json.dumps({
                        "user_input": "帮我下一个11.30号发货的体积5吨的甲烷订单",
                        "flow_id": "methane_order",
                    }, ensure_ascii=False)},
                }],
                "usage": {},
            }

        if self.step == 2 and last and last.get("need_more_info"):
            self.run_id = last.get("run_id")
            return {
                "text": "",
                "tool_calls": [{
                    "id": "c2",
                    "function": {"name": "resume_order", "arguments": json.dumps({
                        "run_id": self.run_id, "user_input": "客户是X化工厂",
                    }, ensure_ascii=False)},
                }],
                "usage": {},
            }

        return {"text": "订单已提交。", "tool_calls": [], "usage": {}}

    async def generate(self, prompt, system_prompt=None, **kw):
        if "可用字段定义" in prompt:
            if "X化工厂" in prompt:
                return {"content": '{"customer":"X化工厂"}'}
            return {"content": '{"product":"甲烷","volume_ton":5,"delivery_date":"11.30号"}'}
        return {"content": "常规订单。"}


@pytest.mark.asyncio
async def test_chat_tool_calling_closes_the_loop(erp_dir, order_checkpointer, monkeypatch):
    """聊天时：识别下单意图 → 追问 → 用户补充 → 写入 Excel

    这是「接入模型后能不能自动下单」的关键路径。
    """
    from app.agents.nodes import _chat_with_tools
    from app.tools.agent_tools import TOOL_REGISTRY, TOOLS_SCHEMA
    from app.workflows.engine import set_deps_provider
    from app.workflows.handlers import erp as erp_mod, register_builtin_handlers
    from app.workflows.loader import FlowRegistry

    register_builtin_handlers()
    llm = _ScriptedLLM()
    set_deps_provider(lambda: {"llm_client": llm})
    FlowRegistry().load(force=True)

    from app.flows import context as fctx
    role_tokens = fctx.bind("tester", "s-tool", role_id="admin")
    try:
        result = await _chat_with_tools(
            llm,
            [{"role": "user", "content": "帮我下一个11.30号发货的体积5吨的甲烷订单"}],
            TOOLS_SCHEMA,
            TOOL_REGISTRY,
            max_rounds=4,
        )
    finally:
        fctx.reset(role_tokens)

    names = [t["name"] for t in result["tool_trace"]]
    assert names == ["create_order", "resume_order"], f"工具调用序列不对: {names}"

    # tool_trace 里只存了 200 字符预览，JSON 会被截断，这里断言预览内可见的部分
    first_preview = result["tool_trace"][0]["output_preview"]
    assert "awaiting_input" in first_preview
    assert "need_more_info" in first_preview
    assert "甲烷" in first_preview, "应带上已抽到的字段，让 LLM 知道进度"
    assert "run_id" in first_preview, "必须回传 run_id，否则 LLM 无法 resume"

    second_preview = result["tool_trace"][1]["output_preview"]
    assert "completed" in second_preview
    assert "order_no" in second_preview

    # Excel 真的有单
    rows = erp_mod.read_pending()
    assert len(rows) == 1
    assert rows[0]["产品"] == "甲烷"
    assert rows[0]["数量(吨)"] == 5
    assert rows[0]["交期"] == "2026-11-30"
    assert rows[0]["客户"] == "X化工厂"
    assert rows[0]["状态"] == "pending"


def test_both_order_tools_registered():
    """create_order 和 resume_order 都必须在工具表里，否则追问无法闭环"""
    from app.tools.agent_tools import TOOL_REGISTRY, TOOLS_SCHEMA

    names = [t["function"]["name"] for t in TOOLS_SCHEMA]
    assert "create_order" in names
    assert "resume_order" in names
    assert "create_order" in TOOL_REGISTRY
    assert "resume_order" in TOOL_REGISTRY


@pytest.mark.asyncio
async def test_resume_order_rejects_unknown_run():
    from app.tools.workflow_tools import tool_resume_order

    payload = json.loads(await tool_resume_order("no-such-run", user_input="客户是X"))
    assert "error" in payload


@pytest.mark.asyncio
async def test_resume_order_requires_payload():
    from app.tools.workflow_tools import tool_resume_order

    payload = json.loads(await tool_resume_order("some-run"))
    assert "error" in payload


# ── P0: 重复下单幂等键 ──────────────────────────────

def test_business_idempotency_key_stable_across_retries():
    """同一用户、同一意图（标点/空格差异忽略）必须得到同一个键；
    不同用户或不同内容必须是不同键。run_id 随机性不得再导致重复建单。"""
    from app.tools.workflow_tools import _business_idempotency_key

    k1 = _business_idempotency_key("methane_order", "帮我订 5 吨甲烷，客户是A厂", "alice")
    k2 = _business_idempotency_key("methane_order", "帮我订5吨甲烷客户是A厂", "alice")
    k3 = _business_idempotency_key("methane_order", "帮我订 5 吨甲烷，客户是A厂", "bob")
    k4 = _business_idempotency_key("methane_order", "帮我订 5 吨甲烷，客户是B厂", "alice")

    assert k1 == k2, "标点/空格差异不应产生新幂等键"
    assert k1 != k3, "不同用户的相同请求不得撞键"
    assert k1 != k4, "不同内容的请求不得撞键"


@pytest.mark.asyncio
async def test_create_order_writes_business_key_to_state(monkeypatch):
    """create_order 必须把稳定业务幂等键写进 state.metadata，
    handler 才能跨重试去重（而非依赖每次新建的随机 run_id）。"""
    import app.tools.workflow_tools as wt
    from app.flows import context as fctx

    captured = {}

    class FakeGraph:
        async def ainvoke(self, initial, config=None):
            captured.update(initial)
            return {"status": "running", "slots": {}, "step_results": {}}

        async def aget_state(self, config=None):
            class S:
                values = {}
            return S()

    class FakeFlow:
        id = "methane_order"
        version = 1

        class _Step:
            id = "collect"
        steps = [_Step()]

    class FakeRegistry:
        def get(self, flow_id, version):
            return FakeFlow()

    class FakeEngine:
        def get_graph(self, *a, **kw):
            return FakeGraph()

    async def fake_checkpointer():
        return object()

    monkeypatch.setattr("app.workflows.loader.get_flow_registry", lambda: FakeRegistry())
    monkeypatch.setattr("app.workflows.engine.get_workflow_engine", lambda: FakeEngine())
    monkeypatch.setattr("app.workflows.checkpointer.get_checkpointer", fake_checkpointer)
    monkeypatch.setattr("app.workflows.checkpointer.extract_interrupt_payloads", lambda s: [])

    tokens = fctx.bind("alice", "s1", role_id="admin", user_input="下一个5吨甲烷订单")
    try:
        await wt.tool_create_order("下一个5吨甲烷订单", flow_id="methane_order")
    finally:
        fctx.reset(tokens)

    meta = captured.get("metadata") or {}
    assert meta.get("idempotency_key"), "未写入业务幂等键"
    from app.tools.workflow_tools import _business_idempotency_key
    assert meta["idempotency_key"] == _business_idempotency_key(
        "methane_order", "下一个5吨甲烷订单", "alice"
    )


# ── P0: 提示注入防护 ──────────────────────────────

def test_prompt_templates_isolate_external_content():
    """检索片段与用户问题必须用标签隔离，且声明其中的指令一律不得执行。"""
    from app.prompts.rag import (
        RAG_PROMPT_WITH_CITATION_AND_TIME,
        GENERAL_PROMPT,
        BUSINESS_PROMPT,
        COMPARISON_PROMPT,
    )

    templates = [
        RAG_PROMPT_WITH_CITATION_AND_TIME,
        GENERAL_PROMPT,
        BUSINESS_PROMPT,
        COMPARISON_PROMPT,
    ]
    for tpl in templates:
        assert "安全约束" in tpl, "缺少安全约束声明"
        assert "<question>" in tpl and "{question}" in tpl, "用户问题未隔离"
    for tpl in (templates[0], templates[3]):
        assert "<reference>" in tpl and "{context}" in tpl, "检索资料未隔离"


@pytest.mark.asyncio
async def test_create_order_rejects_unauthorized_role():
    """driver 等只读角色即使被提示注入诱导，也不能通过工具层下单。"""
    from app.flows import context as fctx
    from app.tools.workflow_tools import tool_create_order

    tokens = fctx.bind("driver_user", "s3", role_id="driver")
    try:
        result = __import__("json").loads(
            await tool_create_order("帮我下一个5吨甲烷订单", flow_id="methane_order")
        )
    finally:
        fctx.reset(tokens)

    assert result.get("started") is False
    assert "无权" in result.get("reason", "")
