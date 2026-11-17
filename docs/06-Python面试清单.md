# Python 面试清单（结合本项目 · 可直接背）

> 2026-11-12 整理。考点分布参考 2025–2026 市面高频 Python 题库（面试鸭、CSDN Python 后端面经、Top Python Interview Questions 等），但**全部按本项目重排**：每题给出「标准答法」+「本项目真实落点（文件:行号）」+「追问预案」。
>
> **怎么用**：`docs/03` 是 RAG / Agent 岗专项，`docs/05` 是 16 道 Agent 工程题，这份是**通用 Python 语言关**。面试官问语言基础时看这份；问到项目架构时回到 `docs/03`。
>
> **项目 Python 环境**：`requires-python >=3.10`（`backend/pyproject.toml:5`），运行时镜像 `python:3.10-slim`（`backend/Dockerfile:2`），依赖由 `uv.lock` 锁定（216 个包）。全量后端测试 **183 passed**。
>
> **一个贯穿全篇的答题原则**：能用项目代码佐证的，一定要带一句「我们项目里是这么用的」。面试官分辨「背过」和「写过」，靠的就是这个。

---

## P0 · 开场定位题（几乎必问）

### P0.1 你项目用的 Python 版本？为什么？

**答**：3.10（`pyproject.toml:5` 下限 + `Dockerfile:2` 镜像 `python:3.10-slim`）。选 3.10 主要是 LangGraph / Pydantic v2 / FastAPI 生态在这个版本上最稳，且能用 `X | Y` 联合类型注解、结构化模式匹配，又不用冒 3.12+ 部分 ML 库（paddleocr、torch 旧版本）wheel 不齐的风险。

**追问「锁版本了吗」**：`uv.lock` 锁死全部依赖，Docker 构建走 `uv sync --no-dev --no-cache`；README:306 明确写了「`pyproject.toml` 改完必须重跑 `uv lock`，否则生产镜像缺包在 import 阶段就崩」，CI 建议 `uv lock --check`。

---

## P1 · 语言基础与数据模型（高频送分，但会挖坑）

### P1.1 可变对象 vs 不可变对象？函数传参是值传递还是引用传递？

**答**：不可变 `int/str/tuple/frozenset/bytes`，可变 `list/dict/set`。Python 既不是值传递也不是引用传递，是**传对象引用（pass-by-object-reference）**：函数内对可变对象原地修改会影响外部，重新赋值不会。`tuple` 不可变指的是「元素引用不可改」，里面装个 list 照样能改。

**本项目落点**：`config.py:73` `CORS_ORIGINS: List[str]` 是可变默认值，但 Pydantic `BaseSettings` 会做校验复制，不会跨实例共享；`tracing/tracer.py:24` 用 `Tuple[str, ...]` 存 span 栈（`ContextVar` default 必须是不可变对象，否则多请求共享同一个栈）。

### P1.2 可变默认参数陷阱：`def f(lst=[])` 为什么是 bug？

**答**：默认参数在**函数定义时求值一次**，不是每次调用。多次调用共享同一个 list，互相污染。正确写法 `lst=None` + 函数体内 `if lst is None: lst = []`。

**本项目落点**：全仓库 grep `def .*=[] / ={}` **零命中**——项目里统一用 `Optional[...] = None`（例 `workflows/checkpointer.py:32` `db_path: Optional[str] = None`、`api/auth.py` 的 Request 模型）。**可以主动说：这类坑我们靠 Pydantic 模型 + Optional 默认值从写法上规避了。**

### P1.3 浅拷贝 vs 深拷贝

**答**：`copy.copy()` 只复制容器第一层，内部嵌套对象仍共享引用；`copy.deepcopy()` 递归复制。`a = b` 只是绑定同一个对象，连拷贝都不是。切片 `lst[:]`、`list(lst)`、`dict(d)` 都是浅拷贝。

**本项目落点**：项目**没有** `deepcopy`（grep 零命中），因为 LangGraph 的 state 传递刻意走「返回新 dict 让框架 merge」的不可变风格，而不是原地改对象再传。可以讲一句：「状态机里我更倾向于构造新 state，避免共享引用带来的隐式副作用」。

### P1.4 `*args` / `**kwargs` 是什么？顺序？

**答**：`*args` 收位置参数为 tuple，`**kwargs` 收关键字参数为 dict。定义顺序必须是 `必填 → 默认 → *args → 仅关键字 → **kwargs`。

**本项目落点**：`prompts/memory.py:40-43` `def format(self, *args, **kwargs)` 复写 `str.format` 做提示词模板；`resilience/circuit_breaker.py:134-136` `get_breaker(name, **kwargs)` 透传断路器配置；`services/llm_service.py:34,47` `_create_completion(**kwargs)` 把采样参数透传给 OpenAI 兼容接口。**这是个好例子**：`**kwargs` 最适合"我不想在一层里穷举下游所有参数"的透传场景。

### P1.5 list / tuple / set / dict 的区别与复杂度

**答**：list 有序可变可重复（append O(1) 均摊，按值查找 O(n)）；tuple 有序不可变、可哈希（能当 dict key）；set 无序不重复、查找 O(1)；dict 3.7+ 保持插入顺序、键查找 O(1)。底层 set/dict 是哈希表，所以 key 必须可哈希。

**本项目落点**：`vectorstore/hybrid_store.py` 里 `allowed_doc_ids` 用 set 做 O(1) 权限过滤；`session_store.py` 用 Redis List 而非 Python list 是因为要跨进程 + TTL，不是语言层面的选择——可以顺带讲「数据结构选型先看生命周期，再看复杂度」。

### P1.6 `is` 和 `==` 的区别

**答**：`is` 比较对象身份（`id()`），`==` 调用 `__eq__` 比较值。小整数/短字符串有缓存池，`is` 偶然为 True 是坑，判断值一律用 `==`；判断 `None/True/False` 用 `is`（单例）。

### P1.7 闭包是什么？装饰器本质？

**答**：闭包 = 内部函数引用了外部函数的变量，且外部函数已返回，变量仍被留住（存在 `__closure__` 里）。装饰器是「接受函数、返回函数」的闭包，`@deco` 等价于 `func = deco(func)`。写装饰器务必 `functools.wraps` 保留 `__name__/__doc__`，否则链路追踪和日志全乱。带参装饰器是三层函数。

**本项目落点**：`api/chat.py:28-40` `_spawn_background` 里的 `_on_done` 就是闭包（捕获了 `name`）；装饰器用得最多的是 FastAPI 的 `@router.post(...)`（`api/chat.py:274` 等）——它们本质也是「注册路由 + 包装 handler」。`core/config.py:234` 的 `@lru_cache()` 是标准库装饰器做单例。

**追问「写个计时装饰器」**：给三层结构 + `wraps` + `*args/**kwargs` 透传 + `try/finally` 保证异常时也打日志。

### P1.8 生成器 / 迭代器 / 惰性求值

**答**：迭代器实现 `__iter__ + __next__`；生成器是带 `yield` 的函数，自动实现迭代器协议，**惰性产出、常数内存**。区别：`return` 一次性返回全部、`yield` 每次挂起保留栈帧。适合大文件/流式/无限序列。生成器只能遍历一次。

**本项目落点（强证据）**：
- `api/chat.py:467-583` SSE 流式接口本身是个生成器，逐 `yield "data: ...\n\n"`；
- `services/llm_service.py:113,128` 逐 token `yield delta.content`；
- `api/checkpoints.py:116-117` `history = [h async for h in graph.aget_state_history(...)]` —— **异步生成器必须 `async for` 消费，不能 `await`**，这是真踩过的坑；
- `services/session_store.py:139,181` `async for key in redis.scan_iter(...)` 用 `SCAN` 替代 `KEYS` 避免阻塞，也是惰性迭代思想。

### P1.9 `yield from` / `send()` / `close()` / `GeneratorExit`

**答**：`yield from` 委派子生成器；`gen.send(v)` 把值送回挂起点；`gen.close()` 在挂起点抛 `GeneratorExit`，生成器应借机释放资源（对应 `finally`）。**`GeneratorExit` 继承自 `BaseException`，不是 `Exception`** —— 所以 `except Exception` 兜不住客户端断连。

**本项目落点（诚实短板，主动讲很加分）**：`api/chat.py` SSE 生成器外层是 `except Exception`，客户端断网时抛的是 `GeneratorExit`，**兜不住、没有清理逻辑**；而且 `save_turn` 在生成器最末尾（`:556`），断在中途这一轮服务端零记录。正确修法：请求进来先落问句，`finally` 里存已生成的部分答案 + `status=interrupted`；`_run_graph` 是 `create_task` 起的（`:521`），断连时要 cancel。**这道题能把「懂生成器协议」和「知道自己系统的缺口」一起讲出来。**

---

## P2 · 并发与异步（本项目最强区，重点准备）

### P2.1 GIL 是什么？多线程能不能并行？怎么绕？

**答**：CPython 的全局解释器锁保证同一时刻只有一个线程执行字节码，简化了引用计数的内存管理，但让 CPU 密集型多线程无法用多核。**IO 密集型不受影响**（等待 IO 时释放 GIL）。绕开方式：CPU 密集用 `multiprocessing` / 进程池 / C 扩展（numpy、torch 底层在 C 里释放 GIL），IO 密集用 `asyncio` 或线程池。

**本项目落点（这段是全场最漂亮的回答）**：项目是「混合策略」——
- 本地 embedding / CrossEncoder 重排 / 文档解析 / BM25 这些**CPU 密集**，统一 `asyncio.to_thread(...)` 丢进线程池（C 扩展内部释放 GIL，实际能并行），见 `embedding_service.py:37,47`、`reranker_service.py:59`、`api/documents.py:407-418`、`api/tracing.py:75,96,104`、`memory_manager.py:260,273,475`；
- Redis / Milvus / LLM HTTP 调用这些**IO 密集**，全用 async 客户端（`asyncpg`、`redis.asyncio`、`pymilvus` async、`httpx`）走事件循环，不占线程；
- RAGAS 这种会 segfault / 抢运行时的重活，直接**独立子进程** `ragas_worker.py`，连 GIL 都不在同一个进程里。

一句话收：「GIL 不是要不要用多线程的问题，是按任务类型选 **线程池 / 事件循环 / 子进程** 的问题。」

### P2.2 协程、事件循环、`async/await` 原理

**答**：`async def` 返回协程对象，**不调用就不执行**；`await` 把控制权交还事件循环，事件循环在 IO 就绪时恢复。是**单线程协作式并发**，不是并行，切换点是 `await`，所以协程里绝不能有同步阻塞调用（会卡死整个 loop）。

**本项目落点**：`services/llm_service.py:47` 把同步的 OpenAI SDK `create()` 包在 `asyncio.to_thread` 里，就是「第三方库只有同步 API 时怎么不阻塞事件循环」的标准解法。**这是一道极高频追问，务必能讲出这个具体文件。**

### P2.3 `asyncio.create_task` 有什么坑？

**答（这题能拉开差距）**：`create_task` 后事件循环**只持弱引用**，如果你不保存强引用，任务可能在跑完前被 GC 回收；另外没被 `await` 的任务里抛的异常会静默丢失（只在 GC 时打一行 "Task exception was never retrieved"）。正确做法：用模块级 `set` 存引用 + `add_done_callback` 里 `discard` 并读 `t.exception()` 记日志。

**本项目落点（现成答案，背下来）**：`api/chat.py:23-40`：
```python
# fire-and-forget 后台任务引用集合：asyncio.create_task 只持弱引用，
# 不保存引用的任务可能执行中途被 GC，且异常静默丢失
_background_tasks: set = set()

def _spawn_background(coro, *, name: str) -> asyncio.Task:
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        ...
```
记忆提取这类「不阻塞响应」的活走这里；`:521` 的 `_run_graph` 也是 create_task。

### P2.4 `ContextVar` vs `threading.local`，异步下用哪个？

**答**：`threading.local` 按线程隔离，而 async 里一个线程跑几十个协程，全串了；`ContextVar` 按**上下文（每个 Task 一份副本）**隔离，`create_task` 会复制当前上下文，天然适合请求级 trace_id / request_id。`set()` 返回 token，用 `reset(token)` 回滚，避免父上下文被污染。

**本项目落点（docs/03 Q2.6 有完整版）**：`core/logging_context.py:7-9` 定义 `request_id_var / trace_id_var / task_id_var`；`tracing/tracer.py:23-24` 用 `ContextVar[Tuple[str,...]]` 存 span 栈，`:103` 用 `_ctx_tokens` 保存 token 做回滚。**追问「为什么 default 用不可变 tuple 而不是 list」** → 默认对象在所有上下文间共享，可变会串数据。

### P2.5 `asyncio.Lock` 与单例的线程/协程安全

**本项目落点**：`workflows/checkpointer.py:27-49` 进程内单例用 `asyncio.Lock()` 包住「检查 `_saver` → 创建连接 → `setup()`」的临界区，防止并发首次调用创建多个 `AsyncSqliteSaver`（每个连接起一个后台线程）。注释写了为什么必须单例、以及路径变化时复用旧连接避免已编译 graph 持有的 saver 被提前关闭。**对比 `config.py:234` 用 `@lru_cache` 做的同步单例 —— 一个同步缓存、一个异步锁，正好能讲「单例实现要看初始化是不是 async」。**

### P2.6 进程间通信 / 子进程

**本项目落点**：`backend/ragas_worker.py` 用 **stdin/stdout 传 JSON** 做主进程与 RAGAS 评分子进程的通信（不走 `multiprocessing.Queue`），3 个 worker 并发、超时 600s kill、崩溃降级记 0 分。理由：RAGAS 依赖 torch/uvloop/nest_asyncio，主进程直接 import 会 segfault，用进程隔离把崩溃半径收窄。还用全 0 向量的 `DummyEmbeddings` 桩骗过 RAGAS 的 embedding 初始化，省掉加载 1GB 模型。

### P2.7 异步上下文管理器 / 超时控制

**本项目落点**：`resilience/timeout_controller.py:45` 用 `@asynccontextmanager` 做超时控制，`tracing/tracer.py:171` 用 `@contextmanager`、`tracing/tracer.py:89-90` 手写 `__enter__/__exit__`（span 自动进出栈）。**顺便答「`with` 的原理」**：`__enter__` 返回值绑定给 `as`，`__exit__` 无论是否异常都执行，返回 True 可吞掉异常。

---

## P3 · 面向对象与类型系统

### P3.1 `TypedDict` / `dataclass` / Pydantic `BaseModel` 怎么选？

**答（本项目恰好三种都用了，这题必背）**：

| | 校验 | 运行时开销 | 用途 |
|---|---|---|---|
| `TypedDict` | 无（只给类型检查器看） | 零 | 内部 state schema |
| `dataclass` | 无（省样板代码） | 低 | 内部值对象 |
| Pydantic `BaseModel` | **有**（解析 + 校验 + 序列化） | 较高 | 外部边界（HTTP 请求/响应、配置） |

**本项目落点**：
- `agents/state.py:9` `RAGState(TypedDict)`、`workflows/state.py` `WorkflowState(TypedDict)` —— LangGraph 的 state 高频在节点间流转，零运行时开销最重要；`agents/graph.py:153` 注释还写了「绑定 TypedDict 而非裸 dict，节点写入未声明的键会被拒绝」；
- `api/chat.py:236,243,252`、`api/auth.py:26,68,77` 等 20+ 处 `BaseModel` 做请求/响应校验；
- `core/config.py:12` `Settings(BaseSettings)` 从 `.env` 读配置 + 类型校验；`core/model_config.py:24` `ModelConfig(BaseModel)`。

**一句判断**：**边界用 Pydantic 做校验，内部热路径用 TypedDict 换性能** —— 这是能体现架构判断的答案。

### P3.2 Pydantic v1 → v2 迁移踩过什么坑？

**答**：v2 原生 Pydantic v2，`@validator` → `@field_validator`、`class Config` → `model_config = ConfigDict(...)`、`parse_obj` → `model_validate`、`dict()` → `model_dump()`；v2 默认不再隐式转类型（严格模式）。项目从一开始就按 v2 写（`pyproject.toml:11-12` pydantic>=2.5.3 / pydantic-settings>=2.1.0），且核心 state 是 TypedDict，所以暴露面小、没有迁移包袱。

### P3.3 单例有几种写法？项目用哪种？

**答**：模块级全局（`settings = get_settings()` 本身就是单例）、`@lru_cache` 工厂、`__new__` 重写、元类、装饰器。`__new__` 版要防止 `__init__` 被重复调用。

**本项目落点**：两种都有 —— `core/config.py:234-241` `@lru_cache()` + 模块级 `settings`；`workflows/checkpointer.py:27-49` 全局 `_saver` + `asyncio.Lock`（因为初始化是 async，不能用 lru_cache）。

**追问「单例有什么坏处」**：全局状态难测试、隐藏依赖。项目里 `tests/conftest.py:11` 有 autouse fixture 做 `close_checkpointer()` 清理，就是因为全局单例会让 pytest 线程不退出 —— **这个细节特别能证明真写过测试**。

### P3.4 `__new__` vs `__init__`、MRO / C3、鸭子类型

**答**：`__new__` 负责创建实例（可返回其他类型的实例，是不可变类型/单例的钩子里），`__init__` 只初始化。MRO 用 C3 线性化保证单调性，`super()` 按 MRO 走而非按父类。Python 是鸭子类型（看有没有方法，不看继承），`abc.ABC + @abstractmethod` 才是强制接口。

**本项目落点**：`core/model_config.py:10,17` 用 `class ModelProvider(str, Enum)` —— **继承 `str` 的 Enum 可以直接当字符串用**（`use_enum_values=True`），这是鸭子类型的实用例子；`tools/base_tool.py` 那层是抽象基类风格（全仓库只有 1 处 ABC）。

### P3.5 上下文管理器 / `with` 原理 → 见 P2.7

---

## P4 · 内存、解释器与性能

### P4.1 Python 垃圾回收机制

**答**：**引用计数为主**（计数归零立即释放，实时性好），**标记-清除**解决循环引用，**分代回收**（0/1/2 代，越老检查越稀）降低扫描成本。循环引用 + 带 `__del__` 的对象在旧版本可能泄漏，用 `gc` 模块或 `weakref` 处理。

**本项目落点**：`api/chat.py:23` 的注释「create_task 只持弱引用，不保存引用的任务可能执行中途被 GC」就是引用计数机制的直接后果；`tracing/tracer.py` 的 span 栈用 `ContextVar` + 不可变 tuple 也是为了避免长生命周期上下文泄漏。**追问「怎么排查内存泄漏」**：`tracemalloc` / `objgraph` / `memory_profiler`，长驻服务重点看全局缓存和没被 discard 的 set/dict。

### P4.2 `__slots__` 省内存的原理？项目用了吗？

**答**：默认实例属性存在 `__dict__` 里（哈希表，开销大），`__slots__` 改成固定槽位，省内存、访问略快，代价是不能动态加属性、不能多继承带 dict 的类。

**本项目落点**：**没有用**（grep 零命中）。诚实答：「我们对象数量不多，瓶颈在模型推理和 IO，不在实例字典，没必要上 `__slots__` 牺牲灵活性」。这比硬说用了强。

### P4.3 Python 为什么慢？怎么优化？

**答**：动态类型（运行时类型检查 + 属性查找）、GIL、解释执行、对象模型开销。优化优先级：**先 profile（cProfile / py-spy）再动手** → 换算法/数据结构 → 内置函数与列表推导 → 向量化（numpy）→ C 扩展 → 缓存（lru_cache）→ 并发/进程。

**本项目落点**：`vectorstore` 用 numpy 做向量运算、BM25 打分（自研、避免引入 ES）；`core/config.py:234` `lru_cache` 缓存配置；检索漏斗 `20/20→10→5` 靠提前裁剪减少计算量，而不是靠语言级微优化。

### P4.4 深拷贝 / 循环引用 / 弱引用 → 见 P1.3、P4.1

---

## P5 · 工程实践（区分「写过项目」的分水岭）

### P5.1 异常处理：`except Exception` 会漏掉什么？

**答**：`BaseException` 是根，`Exception` 只是它的子类；`KeyboardInterrupt`、`SystemExit`、`GeneratorExit`、`asyncio.CancelledError`（3.8+ 继承自 BaseException）**都不被 `except Exception` 捕获**。清理逻辑要放 `finally` 或显式 `except BaseException` 后 re-raise。

**本项目落点**：`api/chat.py` SSE 生成器断连（GeneratorExit）兜不住 —— 见 P1.9，这是真实缺口，主动讲。

### P5.2 pytest 怎么写异步测试 / fixture / 参数化

**答**：`@pytest.mark.asyncio`（pytest-asyncio）跑 async 测试；`@pytest.fixture` 做依赖注入，`autouse=True` 全局生效、`yield` fixture 做 teardown；`@pytest.mark.parametrize` 参数化；`monkeypatch` / `unittest.mock` 打桩；`conftest.py` 放共享 fixture。

**本项目落点**：`tests/conftest.py:11` autouse fixture 关 checkpointer 单例；`tests/test_tool_dispatcher.py:239` 起 12 个 `@pytest.mark.asyncio`；`tests/test_workflow_order.py` 覆盖「interrupt → resume 补槽位 → 再 resume → Excel 落库」两轮恢复。`pyproject.toml:83-86` 注册了 `model` marker，会自动跳过需要下载真实权重的用例（`test_embedding_service_init`）——**这个细节能证明你真跑过全量测试（183 passed）**。

### P5.3 装饰器在框架里的实际应用

**本项目落点**：FastAPI 的 `@router.get/post(..., response_model=..., summary=...)`（`api/chat.py:274`、`api/checkpoints.py:45` 等）本质是「注册 + 包装」；`core/config.py:234` `@lru_cache()`；`resilience/timeout_controller.py:45` `@asynccontextmanager`。

### P5.4 依赖管理与虚拟环境

**答**：`uv`（Rust 实现，比 pip 快）、`uv.lock` 锁全量依赖树、`pyproject.toml` 声明、`uv sync --frozen` 复现。对比 `requirements.txt`（只锁顶层）、`poetry`、`conda`。

**本项目落点**：`backend/uv.lock`（216 包）、`pyproject.toml:80-81` 配了清华源、`Dockerfile:19-22` 先 `COPY pyproject.toml uv.lock` 再 `uv sync` —— **这是 Docker 层缓存的标准写法**（依赖没变就不重装）。分了 `base / dev / runtime` 三个 target，dev 才装 pytest。

### P5.5 日志与链路追踪怎么带 request_id

**本项目落点**：`core/logging_context.py` 的 `ContextVar` + `tracing/tracer.py` 的 span 树（每日 JSONL 索引 + details 详情文件），能按 session 回查一次问答全过程。答「线上怎么定位问题」时用这个。

### P5.6 类型注解 / `Optional` / 泛型 / `Literal` / `Annotated`

**本项目落点**：`agents/state.py:5` 一行里就用齐了 `TypedDict, Annotated, Any, Literal`；LangGraph 用 `Annotated[..., reducer]` 声明状态如何合并。答「类型注解运行时生效吗」：不生效（除非 Pydantic / `get_type_hints` 主动读），纯给 mypy / IDE 用 —— 正好呼应 P3.1 的选型。

---

## P6 · 手撕代码（一面常见，结合项目场景）

> 这些是市面通用题，项目里**没有现成实现**（诚实说明），但用项目场景改写后更容易记住，也更贴合你的岗位。

| # | 题 | 要点 | 项目化包装 |
|---|---|---|---|
| P6.1 | 两数之和 | 哈希表 O(n)，边查边存 | 从 chunk 列表里找两个分数凑阈值 |
| P6.2 | 最大子数组和 | Kadane / 前缀和 | 检索分数序列找最优连续片段 |
| P6.3 | LRU 缓存 | `OrderedDict` / 哈希+双向链表 | 配置缓存、模型实例缓存 |
| P6.4 | 扁平化嵌套列表 | 递归 / 生成器 `yield from` | 文档树 → chunk 列表（呼应 P1.8） |
| P6.5 | 大文件逐行读取不爆内存 | 生成器 + `with`，别 `readlines()` | 50MB PDF/Excel 解析 |
| P6.6 | RRF 融合 | `score = Σ 1/(k+rank)`，k=60 | 项目真有实现（`hybrid_store.py`），见 docs/03 Q2.3 |
| P6.7 | 限流器 / 滑动窗口 | deque 或令牌桶 | `PREMIUM_MODEL_MAX_CONCURRENT=10` 的并发闸门 |
| P6.8 | 拓扑排序 / DAG 执行 | 入度表 + BFS | LangGraph `StateGraph` 的节点调度 |
| P6.9 | 反转字符串 / 判断回文 / 去重保序 | 切片、`dict.fromkeys` | 送分题，30 秒清掉 |
| P6.10 | 线程安全的单例 / 生产者消费者 | Lock + Queue | 排产定时任务的队列模型 |

**手撕时的通用套路**：先说思路和复杂度，再写；写完主动报「时间 O(?)、空间 O(?)」；边界（空输入、单元素、重复值）自己先提一句。

---

## 附录 · 10 分钟速记卡

**必背 6 个「项目锚点」（说出口就赢）**
1. `asyncio.to_thread` —— 同步 CPU 密集不阻塞事件循环（`embedding_service.py:37`）
2. `create_task` 弱引用 + set 保存 + done callback（`chat.py:23-40`）
3. `ContextVar` 按 Task 隔离，`threading.local` 在 async 下失效（`logging_context.py:7`）
4. `TypedDict` 零开销 vs `BaseModel` 做边界校验（`agents/state.py:9` / `api/chat.py:236`）
5. 两种单例：`@lru_cache`（同步）/ `asyncio.Lock`（异步）（`config.py:234` / `checkpointer.py:29`）
6. SSE 是生成器，`GeneratorExit` 是 `BaseException`，`except Exception` 兜不住（`chat.py`）

**必背 4 句结论**
- GIL：IO 密集用 asyncio/线程池，CPU 密集靠 C 扩展释放 GIL 或多进程，重活用子进程隔离。
- GC：引用计数为主 + 标记清除解循环引用 + 分代回收降成本。
- 生成器：惰性、常数内存、只能遍历一次；异步生成器用 `async for` 不能 `await`。
- 装饰器：闭包 + `@` 语法糖，务必 `functools.wraps`。

**诚实短板清单（主动讲比被挖出来体面）**
- 没有用 `__slots__` / `deepcopy`（对象量小，不是瓶颈）。
- SSE 断连缺 `finally` 清理、`save_turn` 在末尾导致整轮不入库。
- 全局单例靠 autouse fixture 兜底清理，说明测试对全局状态敏感。
- `uv.lock` 与 `pyproject.toml` 需手工保持同步（CI 未加 `uv lock --check`）。

**和其他文档的分工**
- 语言基础 / 手撕 → 本文件
- RAG 架构、检索链路、评测、Milvus → `docs/03-面试准备.md`
- 16 道 Agent 工程题（含调度器实现） → `docs/05-Agent工程面试题对照回答.md`
- 项目总体设计 / 组内分享 → `docs/01`、`docs/02`、`docs/04`
