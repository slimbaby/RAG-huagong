"""
Workflow 引擎测试

覆盖：
- YAML 加载与版本管理
- 条件/规则求值
- 槽位抽取与类型转换
- 人工审批 interrupt 挂起与恢复
- 状态持久化（跨请求恢复不重跑已完成步骤）
"""

import asyncio
import os
import sys
import tempfile

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.workflows.loader import FlowRegistry
from app.workflows.nodes import cast_value, eval_condition, eval_rules
from app.workflows.state import ConditionDef, FlowDef, RuleDef


# ── 加载器 ──

def _write_yaml(directory, name, content):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def test_registry_loads_yaml():
    with tempfile.TemporaryDirectory() as d:
        _write_yaml(d, "flow.yaml", """
id: demo
version: 1
name: 测试流程
slots:
  - {name: amount, type: int, required: true}
steps:
  - {id: collect, type: slot_extract}
  - {id: done, type: end}
""")
        reg = FlowRegistry(definitions_dir=d)
        assert reg.load() == 1
        flow = reg.get("demo")
        assert flow is not None
        assert flow.name == "测试流程"
        assert len(flow.slots) == 1
        assert flow.first_step_id() == "collect"


def test_registry_skips_invalid_file():
    with tempfile.TemporaryDirectory() as d:
        _write_yaml(d, "bad.yaml", "id: [unclosed\n")
        _write_yaml(d, "good.yaml", "id: ok\nsteps:\n  - {id: a, type: end}\n")
        reg = FlowRegistry(definitions_dir=d)
        # 坏文件被跳过，好文件仍加载
        assert reg.load() == 1
        assert reg.get("ok") is not None


def test_registry_picks_latest_version():
    with tempfile.TemporaryDirectory() as d:
        _write_yaml(d, "v1.yaml", "id: demo\nversion: 1\nsteps:\n  - {id: a, type: end}\n")
        _write_yaml(d, "v2.yaml", "id: demo\nversion: 2\nsteps:\n  - {id: b, type: end}\n")
        reg = FlowRegistry(definitions_dir=d)
        reg.load()
        assert reg.get("demo").version == 2
        assert reg.get("demo", 1).version == 1


def test_registry_list_flows():
    with tempfile.TemporaryDirectory() as d:
        _write_yaml(d, "a.yaml", """
id: demo
name: 演示
steps:
  - {id: s1, type: human_approval, approver_role: manager}
""")
        reg = FlowRegistry(definitions_dir=d)
        reg.load()
        flows = reg.list_flows()
        assert len(flows) == 1
        assert flows[0]["id"] == "demo"
        assert flows[0]["requires_approval"] is True


# ── 条件与规则 ──

def test_eval_condition_ops():
    state = {"slots": {"amount": 100, "name": "abc"}, "step_results": {"r": 5}}
    assert eval_condition(ConditionDef(field="amount", op="gt", value=50), state) is True
    assert eval_condition(ConditionDef(field="amount", op="gt", value=500), state) is False
    assert eval_condition(ConditionDef(field="amount", op="gte", value=100), state) is True
    assert eval_condition(ConditionDef(field="amount", op="eq", value=100), state) is True
    assert eval_condition(ConditionDef(field="name", op="contains", value="b"), state) is True
    assert eval_condition(ConditionDef(field="r", op="lt", value=10), state) is True
    assert eval_condition(ConditionDef(field="name", op="truthy"), state) is True
    assert eval_condition(ConditionDef(field="missing", op="gt", value=1), state) is False


def test_eval_condition_reads_step_results():
    state = {"slots": {}, "step_results": {"total": 300}}
    assert eval_condition(ConditionDef(field="total", op="gte", value=100), state) is True


def test_eval_rules_reports_errors():
    state = {"slots": {"quantity": 0}}
    rules = [
        RuleDef(field="quantity", op="gt", value=0, error="必须大于0"),
        RuleDef(field="quantity", op="lte", value=5000, error="超限"),
    ]
    errors = eval_rules(rules, state)
    assert len(errors) == 1
    assert "必须大于0" in errors[0]


def test_eval_rules_all_pass():
    state = {"slots": {"quantity": 100}}
    rules = [RuleDef(field="quantity", op="gt", value=0)]
    assert eval_rules(rules, state) == []


# ── 类型转换 ──

@pytest.mark.parametrize("raw,target,expected", [
    ("100", "int", 100),
    ("100.5", "int", 100),
    ("3.14", "float", 3.14),
    ("true", "bool", True),
    ("是", "bool", True),
    ("no", "bool", False),
    (123, "str", "123"),
])
def test_cast_value(raw, target, expected):
    assert cast_value(raw, target) == expected


def test_cast_value_invalid_returns_raw():
    assert cast_value("abc", "int") == "abc"
    assert cast_value(None, "int") is None


# ── 引擎端到端（含 interrupt 与持久化）──

class FakeLLM:
    """模拟 LLM 客户端：按预设返回 JSON"""

    def __init__(self, responses):
        self.responses = responses
        self.calls = 0

    async def generate(self, prompt, system_prompt=None, **kwargs):
        self.calls += 1
        for key, value in self.responses.items():
            if key in prompt:
                return {"content": value}
        return {"content": "{}"}


@pytest.fixture
def flow_dir():
    with tempfile.TemporaryDirectory() as d:
        _write_yaml(d, "demo.yaml", """
id: demo
version: 1
name: 演示流程
slots:
  - {name: amount, type: int, required: true}
  - {name: owner, type: str, required: false}
steps:
  - id: collect
    type: slot_extract
  - id: check
    type: rule_check
    rules:
      - {field: amount, op: gt, value: 0, error: 金额必须大于0}
    on_fail: reject
  - id: approve
    type: human_approval
    approver_role: manager
    on_fail: reject
  - id: done
    type: end
  - id: reject
    type: end
""")
        yield d


@pytest_asyncio.fixture
async def checkpointer(tmp_path):
    """提供 checkpointer，并在用例结束后关闭连接

    aiosqlite 每个连接会起一个后台线程，不关闭的话 pytest 退出时会挂住。
    """
    from app.workflows.checkpointer import close_checkpointer, get_checkpointer

    cp = await get_checkpointer(str(tmp_path / "wf.db"))
    try:
        yield cp
    finally:
        await close_checkpointer()


@pytest.mark.asyncio
async def test_engine_completes_with_approval(flow_dir, checkpointer):
    """完整流程：抽槽位 -> 规则通过 -> 审批通过 -> 结束"""
    from app.workflows.engine import WorkflowEngine, set_deps_provider
    from langgraph.types import Command

    llm = FakeLLM({"可用字段定义": '{"amount": 500}'})
    set_deps_provider(lambda: {"llm_client": llm})

    registry = FlowRegistry(definitions_dir=flow_dir)
    registry.load()
    engine = WorkflowEngine(registry=registry)

    graph = engine.get_graph("demo", 1, checkpointer=checkpointer)

    run_id = "test_run_1"
    config = {"configurable": {"thread_id": run_id}}
    result = await graph.ainvoke({
        "run_id": run_id,
        "flow_id": "demo",
        "flow_version": 1,
        "slots": {},
        "step_results": {},
        "current_step": "collect",
        "goto": None,
        "pending_approval": None,
        "status": "running",
        "error": None,
        "metadata": {},
        "user_input": "帮我采购 500 元的物料",
        "pending_question": None,
    }, config=config)

    # 槽位抽取后应在审批处挂起
    assert result["slots"]["amount"] == 500
    assert result["status"] == "running"

    # 审批恢复
    result = await graph.ainvoke(Command(resume={"approved": True, "comment": "同意"}), config=config)
    assert result["status"] == "completed"
    assert result["step_results"]["approve"]["approved"] is True


@pytest.mark.asyncio
async def test_engine_rejects_on_rule_failure(flow_dir, checkpointer):
    """规则校验失败 -> 跳到 reject 结束"""
    from app.workflows.engine import WorkflowEngine, set_deps_provider

    llm = FakeLLM({"可用字段定义": '{"amount": 0}'})
    set_deps_provider(lambda: {"llm_client": llm})

    registry = FlowRegistry(definitions_dir=flow_dir)
    registry.load()
    engine = WorkflowEngine(registry=registry)

    graph = engine.get_graph("demo", 1, checkpointer=checkpointer)

    run_id = "test_run_2"
    config = {"configurable": {"thread_id": run_id}}
    result = await graph.ainvoke({
        "run_id": run_id,
        "flow_id": "demo",
        "flow_version": 1,
        "slots": {},
        "step_results": {},
        "current_step": "collect",
        "goto": None,
        "pending_approval": None,
        "status": "running",
        "error": None,
        "metadata": {},
        "user_input": "采购 0 元的物料",
        "pending_question": None,
    }, config=config)

    # 规则失败后直接结束（不该到审批）
    assert result["status"] == "failed"
    assert "金额必须大于0" in (result.get("error") or "")
    assert "approve" not in result["step_results"]


@pytest.mark.asyncio
async def test_engine_resume_does_not_rerun_llm(flow_dir, checkpointer):
    """恢复执行时前面的步骤不重跑（LLM 调用次数不增加）"""
    from app.workflows.engine import WorkflowEngine, set_deps_provider
    from langgraph.types import Command

    llm = FakeLLM({"可用字段定义": '{"amount": 500}'})
    set_deps_provider(lambda: {"llm_client": llm})

    registry = FlowRegistry(definitions_dir=flow_dir)
    registry.load()
    engine = WorkflowEngine(registry=registry)

    graph = engine.get_graph("demo", 1, checkpointer=checkpointer)

    run_id = "test_run_3"
    config = {"configurable": {"thread_id": run_id}}
    await graph.ainvoke({
        "run_id": run_id, "flow_id": "demo", "flow_version": 1,
        "slots": {}, "step_results": {}, "current_step": "collect",
        "goto": None, "pending_approval": None, "status": "running",
        "error": None, "metadata": {}, "user_input": "采购物料", "pending_question": None,
    }, config=config)

    calls_after_first = llm.calls
    assert calls_after_first == 1

    await graph.ainvoke(Command(resume={"approved": True}), config=config)
    # 恢复时不应重复调用 LLM
    assert llm.calls == calls_after_first


@pytest.mark.asyncio
async def test_engine_asks_for_missing_slot(flow_dir, checkpointer):
    """必填槽位缺失 -> 挂起追问，补充后继续"""
    from app.workflows.engine import WorkflowEngine, set_deps_provider
    from langgraph.types import Command

    # LLM 第一次抽不到 amount
    llm = FakeLLM({"可用字段定义": "{}"})
    set_deps_provider(lambda: {"llm_client": llm})

    registry = FlowRegistry(definitions_dir=flow_dir)
    registry.load()
    engine = WorkflowEngine(registry=registry)

    graph = engine.get_graph("demo", 1, checkpointer=checkpointer)

    run_id = "test_run_4"
    config = {"configurable": {"thread_id": run_id}}
    await graph.ainvoke({
        "run_id": run_id, "flow_id": "demo", "flow_version": 1,
        "slots": {}, "step_results": {}, "current_step": "collect",
        "goto": None, "pending_approval": None, "status": "running",
        "error": None, "metadata": {}, "user_input": "我要采购点东西", "pending_question": None,
    }, config=config)

    # 状态快照里应有 slot_request 挂起
    snapshot = await graph.aget_state(config)
    tasks = getattr(snapshot, "tasks", None) or []
    interrupts = []
    for t in tasks:
        for i in (getattr(t, "interrupts", None) or []):
            interrupts.append(getattr(i, "value", None))
    assert interrupts, "应挂起等待槽位补充"
    assert interrupts[0]["type"] == "slot_request"
    assert "amount" in interrupts[0]["question"]

    # 直接补充槽位值恢复
    result = await graph.ainvoke(Command(resume={"slots": {"amount": 200}}), config=config)
    assert result["slots"]["amount"] == 200
    # 补充后进入审批挂起
    assert result["status"] == "running"


@pytest.mark.asyncio
async def test_state_survives_new_engine_instance(flow_dir, checkpointer):
    """新进程/新引擎实例仍能恢复流程（状态在 checkpoint 里，不在内存）"""
    from app.workflows.engine import WorkflowEngine, set_deps_provider
    from langgraph.types import Command

    llm = FakeLLM({"可用字段定义": '{"amount": 500}'})
    set_deps_provider(lambda: {"llm_client": llm})

    registry = FlowRegistry(definitions_dir=flow_dir)
    registry.load()

    engine1 = WorkflowEngine(registry=registry)
    graph1 = engine1.get_graph("demo", 1, checkpointer=checkpointer)
    run_id = "test_run_5"
    config = {"configurable": {"thread_id": run_id}}
    await graph1.ainvoke({
        "run_id": run_id, "flow_id": "demo", "flow_version": 1,
        "slots": {}, "step_results": {}, "current_step": "collect",
        "goto": None, "pending_approval": None, "status": "running",
        "error": None, "metadata": {}, "user_input": "采购物料", "pending_question": None,
    }, config=config)

    # 全新引擎实例（模拟进程重启）
    engine2 = WorkflowEngine(registry=registry)
    graph2 = engine2.get_graph("demo", 1, checkpointer=checkpointer)
    result = await graph2.ainvoke(Command(resume={"approved": True}), config=config)

    assert result["status"] == "completed"
    assert result["slots"]["amount"] == 500
