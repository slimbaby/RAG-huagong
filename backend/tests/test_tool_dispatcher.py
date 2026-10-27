"""
简化版 Agent 工具调度器的参考实现与测试

这份实现同时是 [docs/05-Agent工程面试题对照回答.md](../../../docs/05-Agent工程面试题对照回答.md)
第 16 题的参考答案。放在这里是为了**让文档里的代码有回归保护**——文档里的示例代码
不应该只能靠人肉 review。

覆盖题目要求的五项能力：
    注册工具 / 按名称执行 / 超时中断 / 失败重试 / 调用记录
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass
class ToolSpec:
    """工具元数据 + 执行器"""
    name: str
    description: str
    parameters: Dict[str, Any]          # JSON Schema
    handler: Callable
    timeout: Optional[float] = None     # 单工具超时（秒）
    max_retries: int = 0                # 最大重试次数
    retryable: Optional[Tuple] = None   # 哪些异常类型可重试
    idempotent: bool = False            # 是否幂等（决定能否安全重试）


@dataclass
class ToolCallRecord:
    """一次工具调用的记录"""
    call_id: str
    name: str
    arguments: Dict[str, Any]
    status: str          # success / error / timeout / retry_exhausted
    result: Any = None
    error: Optional[str] = None
    attempts: int = 0
    duration_ms: int = 0


def backoff(attempt: int) -> float:
    """指数退避（生产环境还应加抖动）"""
    return 0.01 * (2 ** attempt)


class ToolDispatcher:
    """简化版 Agent 工具调度器"""

    def __init__(self, max_rounds: int = 3, default_timeout: float = 30.0):
        self._tools: Dict[str, ToolSpec] = {}
        self._records: List[ToolCallRecord] = []
        self.max_rounds = max_rounds
        self.default_timeout = default_timeout

    # ── 注册 ──

    def register(self, spec: ToolSpec):
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        return list(self._tools.keys())

    # ── 参数校验 ──

    def _validate(self, spec: ToolSpec, args: Dict[str, Any]) -> Optional[str]:
        schema = spec.parameters
        for key in schema.get("required", []):
            if key not in args or args[key] in (None, ""):
                return f"缺少必填参数: {key}"
        props = schema.get("properties", {})
        for key, val in args.items():
            if key not in props:
                return f"未知参数: {key}"
            expected = props[key].get("type")
            if expected == "string" and not isinstance(val, str):
                return f"参数 {key} 应为 string，实际 {type(val).__name__}"
            if expected == "integer" and not isinstance(val, int):
                return f"参数 {key} 应为 integer"
            if expected == "number" and not isinstance(val, (int, float)):
                return f"参数 {key} 应为 number"
        return None

    # ── 单次执行 ──

    async def _execute_once(self, spec: ToolSpec, args: Dict[str, Any]) -> Dict[str, Any]:
        start = time.monotonic()
        try:
            if asyncio.iscoroutinefunction(spec.handler):
                coro = spec.handler(**args)
            else:
                # 同步工具丢线程池，避免阻塞 event loop
                coro = asyncio.to_thread(spec.handler, **args)
            # wait_for 是真中断；上下文管理器做不到
            result = await asyncio.wait_for(coro, timeout=spec.timeout or self.default_timeout)
            return {
                "status": "success",
                "result": result,
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        except asyncio.TimeoutError:
            return {
                "status": "timeout",
                "error": f"工具 {spec.name} 超时（{spec.timeout or self.default_timeout}s）",
                "duration_ms": int((time.monotonic() - start) * 1000),
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "duration_ms": int((time.monotonic() - start) * 1000),
            }

    # ── 按名称执行 ──

    async def execute(self, name: str, args: Dict[str, Any],
                      call_id: str = None) -> ToolCallRecord:
        call_id = call_id or f"call_{len(self._records)}"
        spec = self.get(name)

        if spec is None:
            rec = ToolCallRecord(call_id, name, args, "error", None, f"未知工具 {name}", 0, 0)
            self._records.append(rec)
            return rec

        # 参数校验失败不重试——重试也不会变好，应该把错误喂回模型
        invalid = self._validate(spec, args)
        if invalid:
            rec = ToolCallRecord(call_id, name, args, "error", None, invalid, 0, 0)
            self._records.append(rec)
            return rec

        attempts = 0
        last: Dict[str, Any] = {}
        for attempt in range(spec.max_retries + 1):
            attempts += 1
            last = await self._execute_once(spec, args)
            if last["status"] == "success":
                break
            # 只有「幂等 + 异常可重试」才重试：
            #   非幂等工具重试 = 重复副作用（可能重复建单）
            if not spec.idempotent:
                break
            if not spec.retryable:
                break
            if attempt < spec.max_retries:
                await asyncio.sleep(backoff(attempt))

        status = last["status"]
        if status == "error" and attempts > 1:
            status = "retry_exhausted"
        rec = ToolCallRecord(
            call_id, name, args, status, last.get("result"),
            last.get("error"), attempts, last["duration_ms"],
        )
        self._records.append(rec)
        return rec

    # ── 批量调用（一次 LLM 返回多个 tool_call）──

    async def execute_batch(self, calls: List[Dict]) -> List[ToolCallRecord]:
        # 串行：多个工具调用之间常有数据依赖，并发会打乱结果顺序
        return [
            await self.execute(c["name"], c.get("arguments", {}), c.get("id"))
            for c in calls
        ]

    # ── 调用记录 ──

    def get_records(self) -> List[ToolCallRecord]:
        return list(self._records)


# ── 测试 ──

import pytest  # noqa: E402


@pytest.fixture
def dispatcher():
    return ToolDispatcher(default_timeout=1.0)


def _spec_double():
    async def handler(x: int):
        await asyncio.sleep(0.01)
        return x * 2

    return ToolSpec(
        name="double", description="翻倍",
        parameters={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
        handler=handler, idempotent=True,
    )


def _spec_flaky():
    def handler():
        raise ValueError("临时故障")

    return ToolSpec(
        name="flaky", description="会失败",
        parameters={"type": "object", "properties": {}},
        handler=handler, max_retries=2, idempotent=True, retryable=(ValueError,),
    )


def _spec_slow():
    async def handler():
        await asyncio.sleep(5)

    return ToolSpec(
        name="slow", description="很慢",
        parameters={"type": "object", "properties": {}},
        handler=handler, timeout=0.2,
    )


def _spec_non_idempotent():
    calls = {"n": 0}

    def handler():
        calls["n"] += 1
        raise ValueError("失败了")

    return ToolSpec(
        name="danger", description="非幂等且会失败",
        parameters={"type": "object", "properties": {}},
        handler=handler, max_retries=3, idempotent=False, retryable=(ValueError,),
    ), calls


@pytest.mark.asyncio
async def test_dispatcher_executes_by_name(dispatcher):
    dispatcher.register(_spec_double())
    rec = await dispatcher.execute("double", {"x": 21})
    assert rec.status == "success"
    assert rec.result == 42
    assert rec.attempts == 1
    assert rec.duration_ms >= 0


@pytest.mark.asyncio
async def test_dispatcher_rejects_duplicate_registration(dispatcher):
    dispatcher.register(_spec_double())
    with pytest.raises(ValueError, match="重复注册"):
        dispatcher.register(_spec_double())


@pytest.mark.asyncio
async def test_dispatcher_validates_required_param(dispatcher):
    dispatcher.register(_spec_double())
    rec = await dispatcher.execute("double", {})
    assert rec.status == "error"
    assert "缺少必填参数" in rec.error
    assert rec.attempts == 0, "参数错不该重试"


@pytest.mark.asyncio
async def test_dispatcher_rejects_unknown_param(dispatcher):
    dispatcher.register(_spec_double())
    rec = await dispatcher.execute("double", {"x": 1, "y": 2})
    assert rec.status == "error"
    assert "未知参数" in rec.error


@pytest.mark.asyncio
async def test_dispatcher_rejects_wrong_type(dispatcher):
    dispatcher.register(_spec_double())
    rec = await dispatcher.execute("double", {"x": "abc"})
    assert rec.status == "error"
    assert "integer" in rec.error


@pytest.mark.asyncio
async def test_dispatcher_unknown_tool(dispatcher):
    rec = await dispatcher.execute("nope", {})
    assert rec.status == "error"
    assert "未知工具" in rec.error


@pytest.mark.asyncio
async def test_dispatcher_retries_idempotent_tool(dispatcher):
    dispatcher.register(_spec_flaky())
    rec = await dispatcher.execute("flaky", {})
    assert rec.status == "retry_exhausted"
    assert rec.attempts == 3, "幂等且异常可重试 → 试满 max_retries+1 次"
    assert "临时故障" in rec.error


@pytest.mark.asyncio
async def test_dispatcher_does_not_retry_non_idempotent(dispatcher):
    """非幂等工具重试 = 重复副作用，不能重试"""
    spec, calls = _spec_non_idempotent()
    dispatcher.register(spec)
    rec = await dispatcher.execute("danger", {})
    assert rec.status == "error"
    assert rec.attempts == 1, "非幂等工具只试一次"
    assert calls["n"] == 1, "handler 只被调用了一次"


@pytest.mark.asyncio
async def test_dispatcher_times_out(dispatcher):
    dispatcher.register(_spec_slow())
    rec = await dispatcher.execute("slow", {})
    assert rec.status == "timeout"
    assert rec.duration_ms >= 190


@pytest.mark.asyncio
async def test_dispatcher_records_calls(dispatcher):
    dispatcher.register(_spec_double())
    dispatcher.register(_spec_flaky())
    await dispatcher.execute("double", {"x": 1})
    await dispatcher.execute("flaky", {})
    await dispatcher.execute("unknown", {})

    records = dispatcher.get_records()
    assert len(records) == 3
    assert [r.name for r in records] == ["double", "flaky", "unknown"]
    assert [r.status for r in records] == ["success", "retry_exhausted", "error"]


@pytest.mark.asyncio
async def test_dispatcher_batch_executes_in_order(dispatcher):
    dispatcher.register(_spec_double())
    recs = await dispatcher.execute_batch([
        {"id": "c1", "name": "double", "arguments": {"x": 1}},
        {"id": "c2", "name": "double", "arguments": {"x": 2}},
    ])
    assert [r.call_id for r in recs] == ["c1", "c2"]
    assert [r.result for r in recs] == [2, 4]


@pytest.mark.asyncio
async def test_dispatcher_sync_tool_does_not_block_event_loop(dispatcher):
    """同步工具必须走线程池，否则阻塞 event loop"""
    def blocking():
        time.sleep(0.2)
        return "done"

    dispatcher.register(ToolSpec(
        name="blocking", description="同步阻塞",
        parameters={"type": "object", "properties": {}},
        handler=blocking,
    ))

    # 并发跑一个同步阻塞工具 + 一个异步任务，验证没有互相阻塞
    async def quick():
        await asyncio.sleep(0.01)
        return "quick"

    t0 = time.monotonic()
    blocking_task = asyncio.create_task(dispatcher.execute("blocking", {}))
    quick_result = await quick()
    elapsed = time.monotonic() - t0

    assert quick_result == "quick"
    assert elapsed < 0.15, "同步工具阻塞了 event loop（耗时 %.2fs）" % elapsed
    await blocking_task
