# Agent 工程面试题 · 基于本项目的对照回答（2026-10-27）

> 这 16 道题是典型的 **Java 后端 + Agent 工程**口径，而本项目是 **Python 3.10 / FastAPI / LangGraph**。
> 回答原则：**项目里有的给真实代码实现；项目里没有的，明确说"我们没用 X，原因是 Y"，再给出 Java 侧的等价做法**——不硬凑。
> 代码位置均可在本仓库核对，路径以 `backend/` 为根。

---

## 1、自我介绍

**参考回答**：

> 我做的是一个面向化工/法规领域的企业级 RAG 知识库系统，Python 3.10 + FastAPI + LangGraph，1.6 万行左右代码，覆盖文档入库、三路检索、答案生成、自动化评测、全链路追踪，**外加一套固定业务流程引擎**。
>
> 技术上我自己实现了三块：手写 BM25 倒排索引（不用 ES）、RRF 融合 + cross-encoder 精排的混合检索、自研 tracer（ContextVar + span 树 + 异步落盘 + 前端火焰图）。另外做了完整的自动化评测体系——8 个指标加权 + 质量门禁 + RAGAS 交叉验证，能用数字证明改动是变好还是变坏。
>
> 最近做的是**固定流程引擎**：把"公司固定流程"（采购下单、生产工单、审批流）用 YAML 声明式定义，运行时由 LangGraph 动态编译成图，支持中途挂起等人审批、几天后断点续跑。落地了第一个真实业务流——化工产品下单，用户在对话框里说一句"下一个 5 吨甲烷订单"，LLM 识别意图后自动抽槽位、追问缺失信息、校验、写入 ERP 待排产池。
>
> 我个人比较在意两件事：**一是把"看起来能跑"和"真的对"分开**——我做了一次全链路复查，发现 4 个真实缺陷（比如多轮补充槽位时用户填的值会丢，因为 LangGraph 恢复时节点是从头重跑的），全部修掉并补了回归测试；**二是敢删自己的东西**——原来写了 178 行自研 Checkpointer，查证后发现零调用点、而且 tracer 早就做得更好，直接删了。

---

## 2、项目介绍

**参考回答**：

**定位**：面向化工/法规领域的企业级 RAG 知识库问答系统 + 固定业务流程引擎。

**规模**：后端 116 个 Python 文件（app 94 + tests）+ 前端 32 个 TS/TSX，约 1.6 万行；docker-compose 9 个服务 + K8s Kustomize（含 CronJob 定时评测）。

**技术栈**：

| 层 | 选型 | 说明 |
|---|---|---|
| 编排 | LangGraph StateGraph | 7 节点 RAG 图 + 独立的 workflow 图 |
| 后端 | FastAPI | REST + SSE 流式 |
| 模型 | OpenAI SDK 兼容 | 主模型 mimo-72b，fast 模型 mimo-v2.5-pro；embedding bge-base-zh-v1.5(768d)；rerank bge-reranker-base(512 截断) |
| 向量库 | Milvus 2.4.17 | HNSW(M=32/efC=256) + COSINE |
| 关键词 | **自研 BM25** | jieba 分词 + 倒排 + pickle 原子持久化，k1=1.5/b=0.75 |
| 融合 | RRF (k=60) | 默认 RRF，加权可切 |
| 会话 | Redis 7 | 短期记忆 60 条 / 24h TTL |
| 长期记忆 | Milvus collection | 语义召回 |
| 业务数据 | PostgreSQL 15 | 用户/角色/权限/评测报告 |
| 流程持久化 | SQLite (`AsyncSqliteSaver`) | LangGraph 官方 checkpointer |

**两条主线**（这是项目最独特的结构）：

```
/api/chat/query ──→ RAG StateGraph（7 节点，无状态单轮）
/api/workflows/* ──→ workflow StateGraph（YAML 动态编译，有 checkpoint）
                      共享 deps（llm_client / tracer / 权限 / lesson）
```

**核心数字**：
- 检索漏斗：每路 top10 → 双路 ×2 = 20 → RRF 融合截 10 → 精排后 5
- 置信度阈值：reranker 0.10 / vector 0.40
- 评测：并发 3，RAGAS 批 10×3 进程，耗时 45.3→17.7 min
- 记忆：短期 60 条/24h，历史注入 12 条×200 字符，去重 md5 + 0.85 相似 LLM 合并，遗忘 30 天 & importance<0.3
- 切分：chunk 1024/100；表格组上限 2048 字符 / 20 行

**质量基线**：全量后端 **183 passed**。

---

## 3、你理解的 AI Agent Harness 是什么，和单纯调大模型 API、固定 Workflow 有什么区别？

**参考回答**：

**我的定义**：Agent Harness 是**大模型外面那一圈让它能真正干活的基础设施**——模型只负责"决策下一步"，harness 负责把决策**安全地落地**。

具体拆开是六件事，本项目都有对应实现：

| harness 能力 | 本项目实现 | 位置 |
|---|---|---|
| 工具注册与元数据 | `TOOLS_SCHEMA` + `TOOL_REGISTRY` | `app/tools/agent_tools.py` |
| 调度/编排 | LangGraph StateGraph，条件边 + 回边 | `app/agents/graph.py` |
| 上下文管理 | 三层记忆 + prompt 拼装 | `app/services/memory_manager.py` |
| 失败兜底 | 熔断 + 重试 + 降级 + 超时 | `app/resilience/` |
| 状态持久化 | 短期 Redis / 长期 Milvus / 流程 SQLite | 同上 |
| 观测 | tracer span 树 + 火焰图 | `app/tracing/tracer.py` |

**和"单纯调 API"的区别**：
- 调 API = 你写死 `LLM → 检索 → 回答`，LLM 只是文本生成器
- Harness = LLM 能**选工具、能循环、能看自己的历史、能从失败里恢复**

**和"固定 Workflow"的区别**（这是本题最关键的辨析）：

| 维度 | 固定 Workflow | Agent Harness |
|---|---|---|
| 控制流由谁定 | **人**（写死在代码/图里） | **模型**（每轮运行时决定） |
| 步数 | 预先确定 | 运行时可变 |
| 分支依据 | 规则/条件字段 | 模型的语义判断 |
| 失败处理 | 静态异常处理 | 语义判断 + 工具返回的错误信息喂回模型 |
| 适合 | 合规审批、状态机、资金操作 | 开放式任务、意图不明确 |

**但两者不是二选一——这是我这个项目最想讲的一点。**

我们项目里两张图是**并列**的，RAG 图是 harness 式的（意图路由 + 工具循环），workflow 图是固定式的（YAML 声明的确定性流程）。**用 workflow 的地方恰恰是那些不该让 LLM 自由发挥的地方**：

- 「查一下甲烷库存」→ 让 LLM 自由判断用哪个工具 ✅
- 「下一个 5 吨甲烷订单」→ 走固定流程，LLM 只负责**识别意图和抽槽位**，后续校验、审批、落库由引擎按 YAML 执行 ❌不能让 LLM 决定跳过审批

我的结论是一句话：**harness 决定 Agent 能有多自由，workflow 决定 Agent 不能有多自由。生产系统里两者是配合关系，不是替代关系。**

> 对应本项目证据：`app/workflows/definitions/methane_order.yaml` 里 `human_approval` 节点是引擎强制的，LLM 没有跳过它的能力。

---

## 4、Agent 执行链路里，Java 后端负责哪一层（工具注册、调度、上下文、记忆、观测）？

**参考回答**：

**先说结论：本项目是 Python 后端，上述五层全部在 Java 后端的角色位置上**——只是实现语言是 Python 而非 Java。分层职责如下：

| 层 | 职责 | 本项目实现 | Java 等价物 |
|---|---|---|---|
| **工具注册** | 工具元数据（name/description/参数 JSON Schema）+ 执行器绑定 | `TOOLS_SCHEMA` / `TOOL_REGISTRY` 两个字典 | Spring `@Bean` + `ToolCallback` 注册表 / `Map<String, Tool>` |
| **调度** | 决定下一步调什么、循环几轮、何时停 | `_chat_with_tools(max_rounds=2)` + LangGraph 条件边 | 自研 Agent Loop / LangChain4j `AiServices` |
| **上下文** | 把本轮需要的知识拼进 prompt | `build_context()` 组装「教训/记忆/参考资料」三段 | 自己拼 `PromptTemplate` 或 `ChatMemory` |
| **记忆** | 短期/长期的存取与召回策略 | `MemoryManager` + `SessionStore`(Redis) + `Milvus collection` | Redis + PG/ES + 自研 `ChatMemoryAdvisor` |
| **观测** | 每步耗时、token、输入输出、错误 | `Tracer` span 树 → JSONL → 前端火焰图 | Micrometer + Tracing(Micrometer Tracing / OTel) |

**Java 侧如果做，我建议的落地方式**：

```java
// 1) 工具注册：注解 + Spring 容器扫描
@Component
public class OrderTool {
    @Tool(name = "create_order",
          description = "提交产品订单到 ERP 待排产池。当用户明确表达下单意图时使用；查询库存/订单状态不要使用。")
    public String createOrder(@ToolParam(description = "用户原始下单请求") String userInput) { ... }
}

// 2) 统一注册到执行器
@Bean
ToolCallbackProvider toolCallbackProvider(ObjectProvider<Tool> tools) { ... }
```

**关键设计点**（这几点 Java 侧同样适用）：

1. **元数据和执行器分离**：`TOOLS_SCHEMA`（给模型看的 JSON Schema）和 `TOOL_REGISTRY`（name → 函数）分开。Schema 可以直接从 Pydantic/注解生成，但**执行器必须独立注册**，因为同名工具可能要换实现（比如测试用 mock、生产用真实）。
2. **调度不写死在业务代码里**：我们用 LangGraph 表达，Java 侧对应的是把「模型 → 工具 → 结果 → 模型」这个循环抽成一个通用 `AgentExecutor`，业务只提供工具。
3. **观测在装饰器层统一注入**，不要让每个工具自己写埋点——我们用 tracer 的 `start_span` 上下文管理器包住节点函数。
4. **记忆的"写"和"读"要异步分离**：我们 `extract_and_store` 是 fire-and-forget 异步的，不能因为记忆抽取慢就阻塞主响应。

---

## 5、Function Calling 在你项目里怎么落地：工具元数据、参数 Schema、分发器、结果回写怎么设计？

**参考回答**：

**四步，全在 `app/agents/nodes.py::_chat_with_tools` 和 `app/tools/agent_tools.py`。**

### 5.1 工具元数据：JSON Schema 直接给模型

```python
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "query_employee_info",
            "description": "查询员工信息表（姓名、城市、年龄）。回答某人的年龄/城市/籍贯，"
                           "或比较多人属性时必须优先使用此工具，不要凭表格记忆回答。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "员工姓名；留空返回全部员工"},
                },
                "required": [],
            },
        },
    },
    # calculate / get_current_date / query_order / query_inventory / query_quality
    # create_order / resume_order（workflow 相关）
]
```

**关键：description 写得比多数人细。** 里面直接写「必须优先使用此工具，不要凭表格记忆回答」——这不是装饰，是**行为约束**。模型选不选工具，很大程度取决于 description 的措辞。

我们下单工具的 description 还专门写了排除项：

> "查询订单状态、查库存、问价格、问交期等只读请求**不要使用本工具**。"

因为「下一个 5 吨甲烷订单」和「查一下甲烷库存」字面极像，误判代价是**真给人建单**。

### 5.2 参数 Schema：跟业务槽位对齐，不跟实现对齐

我们 workflow 流程的槽位定义（YAML）和工具参数是同一套语义：

```yaml
slots:
  - {name: product,      type: str,   required: true,  description: 产品名称}
  - {name: volume_ton,   type: float, required: true,  description: 数量，单位吨}
  - {name: delivery_date, type: date,  required: true,  description: 交期}
  - {name: customer,     type: str,   required: true,  description: 客户名称}
```

### 5.3 分发器：名字 → 执行器，async/sync 都兼容

```python
fn = tool_registry.get(fn_name)
if fn is None:
    output = json.dumps({"error": f"未知工具 {fn_name}"}, ensure_ascii=False)
else:
    try:
        # 工具可能是 async（如 workflow 下单），也可能是普通同步函数
        if inspect.iscoroutinefunction(fn):
            output = await fn(**args)
        else:
            output = fn(**args)
    except Exception as e:
        output = json.dumps({"error": str(e)}, ensure_ascii=False)
```

**三个设计点**：
- **未知工具不抛异常**，返回结构化错误让模型自己纠正（比 500 强）
- **工具异常不中断循环**，把错误信息喂回模型让它换策略
- **`inspect.iscoroutinefunction` 兼容 async**——这是后来补的，因为 workflow 工具是 async 的（要 await checkpointer）

### 5.4 结果回写：必须带 tool_call_id，串成对话历史

```python
assistant_msg = {"role": "assistant", "content": result.get("text") or "", "tool_calls": calls}
msgs.append(assistant_msg)
for tc in calls:
    ...
    msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": output})
```

**`tool_call_id` 必须原样带回**——这是 OpenAI function calling 协议的要求，少了它模型会认为工具结果对不上号。我们还额外做了两件事：

- **工具结果统一 JSON 化**（`json.dumps(..., ensure_ascii=False)`），包括错误。这样模型看到的是结构化数据而非自然语言乱码
- **`tool_trace` 记录每次调用的 name/args/output_preview**，用于排障和前端展示

### 5.5 完整循环

```python
for _ in range(max_rounds + 1):        # max_rounds = 2，即最多 3 轮
    result = await llm_client.chat_with_tools(msgs, tools=tools_schema)
    calls = result.get("tool_calls") or []
    if not calls:                        # 模型不再要工具 → 收敛
        return {"text": ..., "usage": ..., "tool_trace": tool_trace}
    # ... 执行工具、回写 ...
# 轮次用尽，最后一次强制无工具回答
result = await llm_client.chat(msgs)
```

**注意最后的兜底**：轮次用尽时**不是报错，而是强制让模型用已有信息给一个不带工具的最终回答**。宁可答案不完美，也不能让请求挂死。

### Java 侧等价物

LangChain4j 的 `AiServices` / `ToolSpecification` 做的就是这套；`tool_call_id` 对应它的 `ToolExecutionRequest`。手写的话就是上面的 for 循环——**注意两处**：异常要转成结构化错误回喂模型，以及轮次上限要有最终兜底。

---

## 6、Agent 多轮推理怎么防止死循环：最大轮次、重复调用检测、连续失败降级怎么实现？

**参考回答**：

**先诚实说明现状——我们这个项目在这块是「部分实现 + 一个已知缺口」。**

### 6.1 已实现：最大轮次

```python
async def _chat_with_tools(llm_client, messages, tools_schema, tool_registry, max_rounds=2, ...):
    for _ in range(max_rounds + 1):
        ...
    # 轮次用尽，最后一次强制无工具回答
    result = await llm_client.chat(msgs)
```

`max_rounds=2` → 最多 3 轮 LLM 调用。超限后**不是报错，是强制收尾**（见 5.5）。

配置层也预留了：

```python
class IterationLimits(BaseModel):
    agent_max_iterations: int = 10     # Agent最大迭代次数
    rag_max_retries: int = 3
    llm_max_retries: int = 3
    tool_max_retries: int = 2          # 工具调用最大重试次数
```

### 6.2 ⚠️ 已知缺口：`agent_max_iterations` 和 `tool_max_retries` **只是配置，没有接线**

我 grep 过全项目，这两个字段**只出现在 `resilience_config.py` 的定义里，没有任何地方读取它们**。当前实际生效的是 `_chat_with_tools` 里的 `max_rounds=2` 硬编码参数。

这正是我们复查时发现的那类问题——**「看起来有、实际没接线」的框架代码比没写更危险**。所以我把它列为待办。

### 6.3 已实现：底层调用的重试 + 熔断 + 降级

`app/resilience/executor.py` 的 `ResilienceExecutor` 是真正接线的部分：

```python
async def execute_async(self, name, coro_factory, retryable=None, timeout=None, ...):
    breaker = self._breaker(name)
    for attempt in range(self.config.retry.max_attempts):
        if self.config.circuit_breaker.enabled and not breaker.can_execute():
            raise CircuitBreakerError(...)      # 熔断 → 快速失败
        try:
            result = await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
            if self.config.circuit_breaker.enabled:
                breaker.record_success()
            return result
        except Exception as e:
            transient = self._is_transient(e, retryable)
            if transient and self.config.circuit_breaker.enabled:
                breaker.record_failure()        # 失败计数 → 触发熔断
            if transient and attempt < max_attempts - 1:
                await asyncio.sleep(self._backoff_delay(attempt))   # 指数退避 + 抖动
                continue
            raise
```

**要点**：
- **只对瞬态异常重试**（`_is_transient` 按 MRO 类名匹配配置里的可重试异常列表），业务异常不重试
- **指数退避 + 抖动**（`_backoff_delay`），避免惊群
- **熔断器三态**（closed/open/half-open），open 时快速失败，half-open 试探
- **超时用 `asyncio.wait_for`**，不是上下文管理器——后者是"事后判断"，无法中断正在执行的协程（这是我们记录的已知缺陷）
- **同步路径 `execute_sync` 不提供 timeout**，因为同步代码无法安全中断

### 6.4 已实现：整链路降级（单点故障不让请求 500）

| 故障 | 降级行为 |
|---|---|
| reranker 不可用 | 透传过滤结果，不报错 |
| Redis 挂 | 内存兜底 |
| RAGFlow 挂 | 本地解析器 |
| RAGAS 挂 | 该指标记 0 分，不中断评测 |
| 熔断器打开 | 快速失败 + `retry_after` 提示 |

### 6.5 ⚠️ 未实现：重复调用检测、连续失败降级

**重复调用检测**（同一个工具、同一组参数在多轮里反复调）我们**没有做**。当前靠 `max_rounds=2` 兜底。

如果要做，我会在 `tool_trace` 上加检测：维护 `(fn_name, args_hash)` 集合，重复出现就提前终止并把"你已经调用过这个工具"喂回模型。

**连续失败降级**（连续 N 次工具异常就切换策略）也**没有做**。当前是每次异常都把错误喂回模型让它自己纠正——这对"参数写错了"有效，对"服务彻底挂了"会白烧轮次。

**Java 侧怎么做**：
- 重复检测 → 在 `AgentExecutor` 的循环里维护 `Set<ToolCallKey>`，命中就 break
- 连续失败降级 → 维护 `consecutiveFailures` 计数，超阈值切 fallback 策略（换工具 / 走通用回答 / 报错）
- 更彻底的做法是给**工具本身**标注 `idempotent` / `retryable` / `dangerous` 三个属性，调度器据此决定重试、跳过还是要求人工确认

---

## 7、大模型返回的工具调用 JSON 解析失败或参数非法，后端怎么兜底和重试？

**参考回答**：

**我们分三层兜底，但有一层是"静默吞掉"，我认为是缺陷。**

### 7.1 第一层：JSON 解析失败 → 降级为空参数

```python
raw_args = tc.get("function", {}).get("arguments", "{}")
try:
    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
except Exception:
    args = {}          # ← 静默吞掉
```

**问题**：模型返回了坏的 JSON，我们假装它传了空参继续执行。对 `query_employee_info`（name 可空）无所谓，但对 `create_order`（user_input 必填）就是**用空字符串去启动一个下单流程**。

**正确做法**（我们该改成）：解析失败时返回结构化错误**喂回模型**，让它重试一次：

```python
except Exception as e:
    output = json.dumps({
        "error": f"工具参数不是合法 JSON，请重新生成：{e}",
        "hint": f"expected schema: {schema_of(fn_name)}",
    }, ensure_ascii=False)
    continue   # 计入本轮，让模型看到错误
```

Java 侧对应 Jackson 的 `ObjectMapper.readValue` 失败处理。

### 7.2 第二层：参数非法 → 交给业务侧校验

我们不自己写一套参数校验框架，而是**让业务函数自己校验**：

```python
async def tool_create_order(user_input: str, flow_id: str = "methane_order", version: Any = None) -> str:
    if not user_input or not user_input.strip():
        return json.dumps({"error": "user_input 不能为空"}, ensure_ascii=False)
```

返回值是**结构化错误 JSON，不是异常**。异常会被上层的 `except Exception` 转成 `{"error": str(e)}`，信息量少很多。

**workflow 侧还有一层**：槽位类型转换 + 必填校验。

```python
# nodes.py cast_value：int/float/bool/date 转换，失败返回原值
slots[key] = cast_value(raw, slot_defs[key].type)

# 闸门节点：必填缺失不报错，写 pending_question 让上层追问
missing = [s.name for s in required if slots.get(s.name) in (None, "")]
```

**还有一层语义防误触发**（下单工具特有）：

```python
if _looks_like_read_only(user_input):
    return json.dumps({"started": False, "reason": "这是查询类请求，不需要走下单流程"})
```

### 7.3 第三层：工具执行异常 → 错误信息喂回模型

```python
try:
    output = await fn(**args)
except Exception as e:
    output = json.dumps({"error": str(e)}, ensure_ascii=False)
```

**不中断循环**——模型看到错误信息后可能换个参数重试或换工具，这是 harness 的核心价值。

### 7.4 已知缺口

**参数非法目前没有"schema 级"校验**。我们依赖：
1. 模型的 JSON Schema 约束（`required` 字段）
2. 业务函数自己的 if 判断
3. workflow 的槽位类型转换 + 必填校验

**没有** Pydantic 校验工具入参。所以模型如果传了 `volume_ton: "5吨"`（带单位），`cast_value` 会转 float 失败保留原值，然后 rule_check 报错——能兜住，但错误信息是"数量必须大于 0"而不是"类型不对"，对模型自我纠错不够友好。

**Java 侧更好的做法**：用 Bean Validation（JSR-380）在工具入参上声明 `@NotBlank`、`@Min`、`@Pattern`，校验失败时把**具体哪个字段错了**返回给模型。

### 7.5 一个额外的坑：模型返回 content 和 tool_calls 同时为空

我们用 `if not calls: return` 收敛。如果模型既没给内容也没给工具调用（有些模型会返回空），我们会把空文本当答案返回。**更好的做法是检测这种情况并重试一次**（换温度或加提示），这个也没做。

---

## 8、Agent 上下文和记忆怎么管理：短期上下文截断、长期记忆存储、并发会话隔离怎么做？

**参考回答**：

### 8.1 三层记忆架构

```
┌─ 短期（工作记忆）──────────────────────────┐
│ Redis List，key = session:{id}            │
│ LTRIM 保留最近 60 条 + EXPIRE 24h         │
│ 存的是原始对话轮次                          │
└──────────────────────────────────────────┘
┌─ 长期（语义记忆）──────────────────────────┐
│ Milvus 独立 collection: conversation_memories│
│ 向量 + 语义召回（阈值 0.85 合并）           │
│ 存的是"提炼出的事实"，不是原始对话          │
└──────────────────────────────────────────┘
┌─ 工作（拼装上下文）───────────────────────┐
│ build_context() 组装三段塞进 prompt         │
└──────────────────────────────────────────┘
```

### 8.2 短期上下文截断：Redis LTRIM + TTL

```python
async def add_message(self, session_id, role, content, ...):
    pipe = self.redis.pipeline()
    pipe.rpush(key, json.dumps(msg, ensure_ascii=False))
    pipe.ltrim(key, -self.max_messages, -1)   # max_messages = 60
    pipe.expire(key, self.ttl)                 # ttl = 86400 (24h)
    pipe.execute()
```

**为什么用 LTRIM 而不是取的时候切片**：LTRIM 在写入时就裁剪，Redis 内存占用恒定；`lrange` 截断是读时裁剪，内存里仍然堆着 60 条以上。写入侧裁剪对长会话更友好。

**TTL 是硬性隔离**：24h 不用了自动过期。

### 8.3 长期记忆存储：向量 + 语义去重

写入流程（`MemoryManager._store_memory`）：
1. 内容算 **md5 前 16 位**做精确去重（挡完全重复）
2. 算 embedding，查 Milvus 找相似度 ≥ **0.85** 的
3. 命中 → 调 LLM merge（"用户喜欢简洁回答" vs "用户偏好简短回复"语义重复但字面不同，字面去重挡不住）
4. 没命中 → 存 JSONL + 插 Milvus

**遗忘**：召回排序分 = `relevance × exp(-0.05 × 距上次访问天数) × importance`，时间衰减是**乘性**的。后台清理把 30 天未访问且 `importance < 0.3` 的标 expired 并删 Milvus 向量。

**importance 分级**：用户明确指令 0.9+、事实陈述 0.6+、闲聊 0.3+。

### 8.4 上下文拼装：语义位隔离

这是我认为这块最重要的设计。`build_context()` 把内容拼成三段**互不干扰**的段：

```
## 历史教训（同类问题的注意事项）   ← 行为准则
## 长期记忆                          ← 背景信息
## 参考资料                          ← 事实依据（答案只能来自这里）
```

**为什么必须分开**：教训是「这类问题该怎么做」，记忆是「用户是谁」，参考资料是「事实」。混在一起，LLM 会把准则当事实转述出去——我们实测过它会编出"根据知识库记载，法规类问题需注意发布日期"这种鬼话。

**光分段不够，还要在「回答要求」里加硬约束**：「历史教训是行为准则，不是事实依据，不要把它当作知识库内容引用」。

### 8.5 并发会话隔离：三层

| 隔离维度 | 手段 |
|---|---|
| 短期会话 | Redis key 带 session_id，天然隔离 |
| 长期记忆 | 向量都带 `session_id` 过滤条件，召回时按会话过滤 |
| 请求级 | **ContextVar** 存 trace_id 和 span 栈 |

**ContextVar 这块踩过坑**：我们最初用 `threading.local` 存 trace_id，在 asyncio 场景下**失效**——因为多个协程在同一线程里跑，`threading.local` 是线程级的不是协程级的，结果 trace 数据串了。

改用 `contextvars.ContextVar` 后，每个协程有独立的上下文副本：

```python
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("rag_trace_id", default="")
_span_stack_var: contextvars.ContextVar[Tuple[str, ...]] = contextvars.ContextVar("rag_span_stack", default=())
```

**Python 的 `contextvars` 和 Java 的 `ThreadLocal` 差别要特别注意**：Java 里 `ThreadLocal` 在线程池复用时会串数据（要 `remove()`）；Python 的 `ContextVar` 每个 Task 有独立副本，但**如果用 `asyncio.to_thread` 把同步代码丢到线程池，ContextVar 不会自动传播**，需要 `contextvars.copy_context()`。我们自研 BM25 走的就是 `asyncio.to_thread`（因为它是同步 CPU 密集的），那段代码不依赖 ContextVar 所以没踩坑，但这是要注意的点。

### 8.6 记忆抽取的异步化

`extract_and_store` 是 **fire-and-forget**——抽取记忆不能阻塞主响应。我们用 `asyncio.create_task` + 引用保活（否则 task 可能被 GC 提前回收，这是 Python 的已知坑）。

### 8.7 已知缺口

**长期记忆是双写**（JSONL + Milvus），JSONL 全量重写无锁，与 Milvus 可能失同步。这是待改进项。

**会话归属没校验**：`/chat/history/{session_id}` 这类接口没强制校验 session 归属，且 session_id 只有 8 位可枚举——这是我们记录的**仍开放的安全缺陷**。

---

## 9、Spring 里工具执行器怎么注册和生命周期管理，AOP 怎么做点统计每次 Agent 调用？

**参考回答**：

**先说清：本项目是 Python，没有 Spring 也没有 AOP。但这套问题在我们这里有等价答案，而且我觉得其中一点比 Spring AOP 更合适。**

### 9.1 工具注册：装饰器 + 注册表

我们的机制（`app/workflows/nodes.py`）：

```python
HANDLERS: Dict[str, Callable] = {}

def register_handler(name: str, fn: Callable) -> None:
    """注册一个 Python handler（side_effect / notify 用）"""
    HANDLERS[name] = fn

def get_handler(name: str) -> Optional[Callable]:
    return HANDLERS.get(name)
```

业务侧注册：

```python
def register_builtin_handlers() -> None:
    register_handler("erp.write_pending_order", write_pending_order)
    register_handler("erp.notify_order_received", notify_order_received)
```

**生命周期管理**：
- 注册在 **应用 startup 时执行一次**（`app/main.py` 的 `startup_event`）
- 进程内单例，进程存活期间有效
- shutdown 时释放资源（`close_checkpointer()` 关 SQLite 连接）

**跟 Spring 的对应关系**：

| 维度 | 本项目 | Spring |
|---|---|---|
| 注册方式 | 显式 `register_handler()` 调用 | `@Component` + 容器扫描 / `@Bean` |
| 注册时机 | startup 显式调用 | 容器 refresh 时 |
| 生命周期 | 进程级 | Bean 作用域（singleton/request/prototype） |
| 释放 | shutdown 显式关 | `DisposableBean` / `@PreDestroy` |

**我们的注册是显式的，这点我觉得更好**——启动日志里能看到「内置 workflow handler 注册完成」，出问题一眼看出是没注册还是注册错了。而 Spring 扫描虽然省事，但「某个 handler 没被扫到」这类问题排查成本更高。

**我们踩过的坑值得说**：`handlers/__init__.py` 里 `from ..workflows.nodes import ...` 导入层级写错了（handlers 已在 workflows 下，应该是 `..nodes`）。**因为这个模块一直零调用点，测试从没触发过**，所以一直没暴露；真去调 `register_builtin_handlers()` 立刻 `ModuleNotFoundError`。

**教训**：新写的模块如果没有任何调用点，第一次被调用时才会暴露问题——**写完就手动调一次**。

### 9.2 AOP 埋点：我们用 tracer 的上下文管理器，不用 AOP

Spring 里统计 Agent 调用通常上 AOP（`@Around` 切 `AgentExecutor` 的方法）。我们用**显式的 span 上下文管理器**：

```python
tracer = deps.get("tracer")
span_ctx = tracer.start_span(
    SpanType.WORKFLOW_STEP,
    step.display_name(),
    inputs={"flow_id": flow.id, "step_id": step.id, "step_type": step.type},
)
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
```

**为什么不用 AOP（Python 里也没 AOP 生态）**：

1. **AOP 隐式埋点，链路是断的**。Python 没有 AOP，如果靠装饰器自动包装函数，调用链上的每一层都要显式加装饰器——漏一个就少一段 span。我们的做法是**在引擎的节点工厂里统一包**，所有节点自动获得 span，业务代码零侵入且不会漏。
2. **AOP 拿不到业务语义**。AOP 切面只能拿到方法参数，拿不到「这一步是流程的第几步」「跳转到哪」。我们把 `step_id` / `flow_id` / `goto` 都塞进 span 的 attributes，前端火焰图才能画出流程拓扑。
3. **显式代码在异步场景更可控**。`with` 块的边界就是 span 的生命周期，异常路径上 `__exit__` 明确设置 error 状态。

**统计维度**（tracer 记的）：
- 每个 span 的 `duration_ms`（真实计算，不是写死）
- 父子 span 树（`ContextVar` 存 span 栈）
- token 消耗（`record_tokens`）
- 输入输出（`inputs` / `outputs`）

**落盘**：异步 JSONL，摘要/详情两级分离（详情有 `max_body_length` 截断，默认 10000 字符）。

### 9.3 Java 侧我会怎么做

```java
@Aspect @Component
public class AgentCallMetricsAspect {
    @Around("execution(* com.x.agent.AgentExecutor.*(..))")
    public Object around(ProceedingJoinPoint pjp) throws Throwable {
        Metrics.counter("agent.calls").increment();
        long start = System.nanoTime();
        try {
            return pjp.proceed();
        } catch (Throwable t) {
            Metrics.counter("agent.errors").tag("type", t.getClass().getSimpleName()).increment();
            throw t;
        } finally {
            Metrics.timer("agent.latency").record((System.nanoTime()-start)/1e6);
        }
    }
}
```

**但我会加一条 AOP 做不到的**：把「业务语义」通过注解显式传出去。

```java
@AgentStep(stepId = "write_erp", flowId = "methane_order", spanType = "workflow_step")
public void writeOrder(...) { ... }
```

AOP 切面读注解，把 stepId/flowId 塞进 span attributes——这样火焰图既有调用栈又有业务拓扑。**AOP 负责技术埋点，注解负责业务语义，两者配合**。

---

## 10、线程池在 Agent 并发调用工具场景怎么设计：核心参数、异步编排、超时与拒绝策略？

**参考回答**：

**我们项目里没有用线程池做工具并发——但原因值得说，而且有一个真实的坑。**

### 10.1 为什么没用线程池

我们有两个并发场景，用的都不是线程池：

**场景一：RAG 评测的并发**（`app/evaluation/runner.py`）——用 **asyncio.Semaphore**：

```python
self.concurrency = max(1, getattr(settings, "EVAL_CONCURRENCY", 3))   # 默认 3
sem = asyncio.Semaphore(self.concurrency)
...
intermediates = list(await asyncio.gather(*tasks))    # 三阶段并发
```

**场景二：工具调用本身是串行的**——`_chat_with_tools` 里 `for tc in calls:` 逐个 await。这是**有意的**：

- 多个工具调用之间往往有**数据依赖**（第一个的输出是第二个的输入）
- 并发执行会打乱 `tool_call_id` 与结果的对应顺序
- 我们的工具大多有外部副作用（写库、发通知），并发收益小、风险大

### 10.2 我们踩过的真实坑：同步 CPU 密集操作会阻塞 event loop

自研 BM25 是纯 Python 倒排检索，CPU 密集且**同步**。直接在 async 函数里调会**阻塞整个 event loop**。

解法是丢到线程池——用的是 `asyncio.to_thread`（Python 3.9+ 的封装，项目锁定 3.10）：

```python
def _do_bm25():
    for c in chunks:
        content = _get(c, "content", "")
        nl_desc = _get_meta(c, "table_nl_description", "")
        if nl_desc:
            content += " " + nl_desc
        self.bm25.add_document(_get(c, "chunk_id"), content, {...})
    self._save_bm25()

await asyncio.to_thread(_do_bm25)
```

**而且我们把双路检索真并发起来了**（`hybrid_store.py:219`）：

```python
vector_results, bm25_results = await asyncio.gather(
    asyncio.to_thread(self._vector_search, query_vector, top_k * 2, allowed_doc_ids),
    asyncio.to_thread(self._bm25_search, query, top_k * 2, allowed_chunk_ids)
)
```

**注意 `to_thread` 的两个坑**：
1. **ContextVar 不会自动传播**到 executor 线程。我们那段代码不依赖 ContextVar（BM25 不需要 trace_id）所以没出事，但如果要传，得用 `contextvars.copy_context().run(...)`
2. **默认线程池无界**。生产上应该用独立的、有界且有明确名字的 `ThreadPoolExecutor`，便于观测和限流

### 10.3 核心参数（Java 侧）

| 参数 | 我们对应的 | 建议值与理由 |
|---|---|---|
| corePoolSize / maxPoolSize | 线程池 | CPU 密集（BM25/rerank）：core = N_cpu，队列用 `LinkedBlockingQueue`（有界） |
| keepAliveTime | — | 60s，allowCoreThreadTimeOut |
| 拒绝策略 | — | **CallerRunsPolicy**（让提交方自己跑，降速不丢任务）或 `AbortPolicy` + 降级 |
| 队列容量 | — | **必须有界**。无界队列 = 无限延迟 = 雪崩 |

**拒绝策略的选择逻辑**：
- 任务可丢（埋点、统计）→ `DiscardPolicy` + 计数
- 任务不可丢但可延迟（写库、发消息）→ `CallerRunsPolicy`
- 任务不可丢不可延迟 → 快速失败 + 告警，走降级

### 10.4 超时策略

**我们用 `asyncio.wait_for`（真中断）**，不用上下文管理器：

```python
result = await (asyncio.wait_for(coro, timeout=timeout) if timeout else coro)
```

**为什么不用 `async with asyncio.timeout()` 之外的写法**：Python 的 `asyncio.timeout` 上下文管理器在 `__aexit__` 里才抛超时异常，如果协程内部是**不可中断的同步代码**（比如 `time.sleep`），超时不会立即生效——任务已经卡在那了。

**这是我们记录的已知缺陷**：「timeout 上下文管理器事后判断无法中断执行」。Java 侧对应 `Future.get(timeout)` + `cancel(true)`，但注意 `cancel(true)` 是**协作式中断**——如果任务在 `synchronized` 块里或不可中断的 IO 里，一样不会立刻停。

### 10.5 熔断作为拒绝的上一级

我们的顺序是：**熔断器（快速失败）→ 超时 → 重试 → 真正执行**。

```python
if not breaker.can_execute():
    raise CircuitBreakerError(..., retry_after=breaker.recovery_timeout)
```

**熔断打开时直接快速失败**，不排队、不重试。这比让请求在队列里等死好得多——而且 `retry_after` 会告诉调用方什么时候再来。

### 10.6 异步编排

**LangGraph 本身就是编排器**——条件边、回边、并行分支都由图表达，不需要手写编排。

我们图里的并行场景：

```python
halluc_result, consistency_result, judge_result = await asyncio.gather(
    self._eval_hallucination(...),
    self._eval_consistency(...),
    self._eval_llm_judge(...),
)
```

`asyncio.gather` 默认任一异常会取消其余——如果要"部分失败不中断"，要用 `return_exceptions=True` 并在收集后逐个判类型。**我们评测里是逐个 catch 的，不是靠 gather 的默认值。**

**Java 侧等价**：`CompletableFuture.allOf` + `handle`（而非 `thenApply`），或者虚拟线程（Java 21+）。

---

## 11、Redis 在 Agent 系统里能承担什么角色：会话状态、限流、工具结果缓存、分布式锁？

**参考回答**：

**我们项目里 Redis 只承担一个角色：短期会话存储。其他三个我们都没用——而且我判断其中两个在 Agent 场景下是应该避免的。**

### 11.1 我们实际用的：会话状态

```python
class SessionStore:
    def __init__(self, redis_url: str, max_messages: int = 60, ttl: int = 86400, password: str = ""):
```

两个 key：
- `session:{id}` → List，存对话轮次
- `session:meta:{id}` → Hash，存会话元信息（创建时间、最后活跃等）

写入用 pipeline 批量执行（减少 RTT）：

```python
pipe = self.redis.pipeline()
pipe.rpush(key, json.dumps(msg, ensure_ascii=False))
pipe.ltrim(key, -self.max_messages, -1)
pipe.expire(key, self.ttl)
pipe.execute()
```

**为什么用 Redis 而不是 PG**：
- 会话数据是**高频写、低价值、生命周期短**（24h TTL）
- LTRIM + TTL 是 Redis 原生能力，PG 得自己写定时清理
- 读多写多，需要低延迟

### 11.2 限流：没做，但应该做

Agent 场景的限流和普通 API 不同——**一次用户请求可能触发 N 次 LLM 调用 + M 次工具调用**，成本是普通 API 的几十倍。

我们目前的保护是：
- `max_rounds=2` 限制了单请求的 LLM 调用次数
- 熔断器保护了后端依赖

**但没有按用户/租户限流**。如果要做，Redis 的 `INCR` + `EXPIRE` 是最直接的：

```python
key = f"ratelimit:{user_id}:{minute}"
count = await redis.incr(key)
if count == 1:
    await redis.expire(key, 60)
if count > LIMIT:
    raise RateLimitError(retry_after=60)
```

### 11.3 工具结果缓存：慎用，Agent 场景下大多是错的

**我认为这个角色在 Agent 场景下大多数情况不该用。**

原因：工具结果依赖上下文。`query_inventory("甲烷")` 的结果在同一时刻对不同用户是一样的——这种**纯读、时间无关**的结果缓存是安全的（key = 用户 + 工具名 + 参数哈希 + 时间窗）。

但：
- `create_order` 绝不能缓存——它是**写操作**，缓存会导致重复建单
- `query_order` 加了时间维度后，缓存失效逻辑比不缓存还容易出错
- 我们的 `create_order` 有幂等键 `run_id:step_id`——**幂等 ≠ 缓存**。幂等是"同一次操作重试不重复"，缓存是"不同时间同一查询复用"。混淆这两者会出事。

如果真要做缓存，我建议：
- 只对**显式标记 `cacheable=True`** 的工具开
- key 必须包含**完整参数哈希**（不只是工具名）
- TTL 要短
- **写操作永远不进缓存**

### 11.4 分布式锁：Agent 场景下更该用幂等，不是锁

我们做下单时选的是**幂等键**而不是分布式锁：

```python
idempotency_key = f"{run_id}:{step.id}"    # run_id:step_id
# 写 ERP 时用这个键 upsert，命中返回已有单号
```

**为什么不用分布式锁**：

| | 分布式锁 | 幂等键 |
|---|---|---|
| 解决 | 并发同时执行 | 重试/重入导致重复执行 |
| 失败模式 | 锁超时/续期失败/脑裂 | 无（天然幂等） |
| 跨系统 | 需要分布式锁服务 | 一个唯一索引就够 |
| 我们的场景 | 不需要 | 正好对上 |

**关键点**：LangGraph 恢复时节点**会重入**（这是框架"从头重跑"的语义），所以"同一次流程被重复执行"是常态而非异常——**幂等是必须项，锁是可选项**。

**什么时候才真需要 Redis 分布式锁**：
- 多个 worker 同时处理**同一批**待办（比如多个排产 worker 抢同一批订单）
- 这时锁粒度是"批次/订单号"，而且必须配幂等兜底——**锁会过期，幂等不会**

**我们的场景恰好相反**：单条流程实例由一个 worker 处理，重入靠幂等解决。

### 11.5 我们踩过的坑

**Redis 不可用时不能挂**。`SessionStore.connect()` 失败时 `self.redis = None`，后续所有操作走内存兜底：

```python
if self.redis:
    ...  # Redis 路径
else:
    ...  # 内存兜底
```

原则：**Redis 挂掉不能让问答主链路 500**。

---

## 12、Kafka 或 RocketMQ 在 Agent 任务里怎么用：异步化、事件驱动、失败重试和削峰？

**参考回答**：

**我们项目里没有引入 MQ。这是刻意的选择，理由比"没来得及"更值得说。**

### 12.1 为什么没用 MQ

我们审视过引入 MQ 的三个理由，逐个否掉：

**理由一「异步化」→ 不成立**。我们真正需要异步的两处，都用更轻的方案解决了：
- 记忆抽取 → `asyncio.create_task` fire-and-forget（同进程，够了）
- 文档解析 → 进程内异步 + **文件 checkpoint**（断点续传）

引入 MQ 会带来"消息丢失/重复/乱序"三个新问题，收益只是"解耦"，而我们本来就没有跨服务解耦需求。

**理由二「事件驱动」→ 需求不存在**。我们的事件流是**单请求内的**（一个 trace 里 7 个节点），用 tracer 的 span 树表达更直接。MQ 适合的是**跨系统的长流程编排**——而我们真正的长流程（固定流程引擎）已经用 LangGraph 的 checkpoint 解决了，比 MQ 更合适。

**理由三「削峰」→ 场景不匹配**。我们的峰值来自**用户问答**（同步请求，用户在等），削峰对它无意义——用户等 10 秒拿到答案 vs 30 秒拿到答案，体验差别巨大。真正能削峰的是**评测任务**，而那个我们已经用 K8s CronJob + 分层抽样解决了。

### 12.2 我们实际用的两个"轻量替代"

**替代一：K8s CronJob 跑批**（定时评测）

```yaml
# k8s/kustomization.yaml 里有 Eval CronJob
```

**替代二：文件 checkpoint + 断点续传**（文档解析）

```python
def _get_checkpoint_path(file_hash: str) -> str:
    ...
def _compute_file_hash(content: bytes) -> str:
    ...
```

同一个文件重复上传不重复解析，因为按 content hash 存了进度 checkpoint。

### 12.3 什么时候这个项目该上 MQ

诚实地讲，有三个场景值得上：

**场景一：Agent 任务耗时长、用户不该同步等**。比如"生成一份完整的工艺分析报告"要跑 5 分钟——这时应该 `POST /tasks` 返回 task_id，用 MQ 承接执行，完成后 WebSocket/企微推送。

**场景二：多个下游系统订阅同一事件**。比如"下单完成"这个事件，要同时通知 ERP、排产系统、给客户发短信。现在是 handler 里顺序调用，MQ 能让订阅方解耦。

**场景三：多 worker 抢同一批任务**。比如 1000 个待排产订单，要 10 个 worker 并行处理——这时需要 MQ 分发 + 幂等兜底。

### 12.4 如果上，会怎么设计

**Topic 划分**：

| Topic | 生产者 | 消费者 | 语义 |
|---|---|---|---|
| `agent.task` | API 层 | Agent worker | 至少一次 |
| `agent.result` | Agent worker | 通知服务 | 至少一次 |
| `agent.audit` | 全链路 | 审计/分析 | 至少一次，可丢 |

**失败重试**：不靠 MQ 的原生重试，靠**消费端幂等 + 死信队列**。

```java
@KafkaListener(topics = "agent.task", groupId = "agent-worker")
public void onTask(AgentTask task) {
    String idemKey = task.runId() + ":" + task.stepId();   // 幂等键
    if (processed(idemKey)) return;                        // 已处理过，直接跳过
    try {
        executor.execute(task);
        markProcessed(idemKey);
    } catch (Exception e) {
        if (e.isTransient()) throw e;                      // 抛回去让 MQ 重投
        dlqProducer.send(task, e);                         // 不可恢复 → 进死信
    }
}
```

**关键**：**重试次数不能无限**。MQ 重投 + 消费端重试会叠乘。我们会配 `max.poll` + 业务侧重试计数 + 超阈值进死信。

**削峰**：
- 消费端用 `concurrency` 配置并发度，靠 DB/下游承载能力定，不靠 MQ 堆消息
- MQ 只做**流量整形**（比如把突发 1000 QPS 拉平成 100 QPS 消费），不做无限制堆积
- **堆积告警**：lag 超过阈值要告警，否则队列积压到用户请求超时时才发现

### 12.5 一个更实用的中间态：不用 MQ，用"任务表 + 轮询"

如果只是想让长任务异步化，**引入 MQ 之前可以先上任务表**：

```sql
CREATE TABLE agent_tasks (
    id BIGSERIAL PRIMARY KEY,
    run_id VARCHAR(64) NOT NULL,
    status VARCHAR(16) NOT NULL,   -- pending/running/succeeded/failed
    payload JSONB NOT NULL,
    attempts INT DEFAULT 0,
    next_retry_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_agent_tasks_pending ON agent_tasks(status, next_retry_at)
    WHERE status = 'pending';
```

worker 轮询 `SELECT ... WHERE status='pending' AND next_retry_at <= now() FOR UPDATE SKIP LOCKED LIMIT 10`。

**好处**：天然有状态可查（用户能查"我的任务到哪了"）、天然支持重试（改 next_retry_at）、天然幂等（用 run_id + 唯一索引）、不引入新中间件。**MQ 的很多能力这个方案都能覆盖，且可观测性更好。**

我们的固定流程引擎其实已经接近这个模式了——用 LangGraph 的 SQLite checkpoint 替代了任务表。

---

## 13、MySQL 里 Agent 对话、任务、工具调用记录怎么建表，怎么设计索引和分区？

**参考回答**：

**我们用的不是 MySQL，是 PostgreSQL。但我先说表设计，再说 MySQL 的差异。**

### 13.1 我们的实际表结构

**PG 存业务数据，Redis 存会话，Milvus 存向量和长期记忆，SQLite 存流程 checkpoint。**

```sql
CREATE TABLE IF NOT EXISTS eval_cases (
    id VARCHAR(64) PRIMARY KEY,
    data JSONB NOT NULL,
    layer VARCHAR(16) DEFAULT 'core',      -- 评测分层：core / sample
    ...
);
CREATE TABLE IF NOT EXISTS eval_reports (
    eval_id VARCHAR(64) PRIMARY KEY,
    data JSONB NOT NULL,
    avg_score FLOAT,
    ...
);
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    ...
);
CREATE TABLE IF NOT EXISTS roles (
    role_id VARCHAR(32) PRIMARY KEY,
    role_name VARCHAR(64) NOT NULL,
    scope_ids JSONB NOT NULL DEFAULT '[]'::jsonb,   -- 权限范围
    ...
);
CREATE TABLE IF NOT EXISTS user_roles (
    username VARCHAR(64) NOT NULL,
    role_id VARCHAR(32) NOT NULL REFERENCES roles(role_id) ON DELETE CASCADE,
    PRIMARY KEY (username, role_id)
);
CREATE TABLE IF NOT EXISTS doc_scopes (
    doc_id VARCHAR(64) NOT NULL,
    scope_id VARCHAR(64) NOT NULL,
    PRIMARY KEY (doc_id, scope_id)
);
```

### 13.2 一个设计选择：JSONB 存整个 payload

`eval_cases.data` 和 `eval_reports.data` 都是 **JSONB**，不是拆成几十个字段。

**为什么**：评测用例的 schema 是**会演进的**——加一个指标就要加字段，拆表意味着每次改 schema。JSONB 让 schema 变成可选，同时 PG 还能对内部字段建 GIN 索引：

```sql
CREATE INDEX idx_eval_cases_data ON eval_cases USING gin(data jsonb_path_ops);
```

**代价**：不能对 JSONB 内部字段做高效的范围查询/排序。要查的话用表达式索引：

```sql
CREATE INDEX idx_eval_reports_score ON eval_reports ((data->>'hallucination_score'));
```

**MySQL 的对应**：MySQL 5.7+ 也支持 JSON 类型和 `JSON_EXTRACT` + 生成列：

```sql
ALTER TABLE eval_cases ADD COLUMN created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3);
ALTER TABLE eval_cases ADD INDEX idx_data (CAST(data->>'$.layer' AS CHAR(16)));
```

**MySQL 没有 JSONB**，索引 JSON 内部字段的常规做法是**生成列 + 普通索引**：

```sql
ALTER TABLE eval_cases
  ADD COLUMN layer VARCHAR(16) GENERATED ALWAYS AS (data->>'$.layer') STORED,
  ADD INDEX idx_layer_created (layer, created_at);
```

### 13.3 Agent 对话记录：我们的取舍

**对话原文我们没存 PG，存 Redis（24h TTL）**。理由：对话数据量大、生命周期短、价值随时间衰减。

**但工具调用记录要长期存**——这是审计和排障需要。我们的做法是：

- **实时**：tracer 落 JSONL（`./logs/traces`），异步写，摘要/详情两级分离
- **长期**：如果需要入库，应该是独立的 `agent_tool_calls` 表

**如果让我设计（MySQL 版）**：

```sql
-- 1) 对话会话
CREATE TABLE agent_sessions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id VARCHAR(64) NOT NULL,
    user_id BIGINT UNSIGNED NOT NULL,
    agent_id VARCHAR(64) NOT NULL DEFAULT 'default',
    title VARCHAR(255) DEFAULT '',
    status VARCHAR(16) NOT NULL DEFAULT 'active',  -- active/archived
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_session_id (session_id),
    KEY idx_user_updated (user_id, updated_at)
) ENGINE=InnoDB;

-- 2) 消息
CREATE TABLE agent_messages (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    session_id VARCHAR(64) NOT NULL,
    seq INT UNSIGNED NOT NULL,             -- 会话内序号，保证顺序
    role VARCHAR(16) NOT NULL,             -- user/assistant/tool/system
    content MEDIUMTEXT NOT NULL,
    tool_calls JSON DEFAULT NULL,          -- assistant 的工具调用请求
    tool_call_id VARCHAR(128) DEFAULT NULL, -- tool 消息关联
    token_in INT UNSIGNED DEFAULT 0,
    token_out INT UNSIGNED DEFAULT 0,
    latency_ms INT UNSIGNED DEFAULT 0,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_session_seq (session_id, seq),   -- 幂等：同一 seq 只写一次
    KEY idx_session_created (session_id, created_at)
) ENGINE=InnoDB;
```

**`UNIQUE KEY uk_session_seq (session_id, seq)` 是关键**——它同时解决两件事：保证消息顺序、保证重试写入不重复。

### 13.4 工具调用记录表（审计用）

```sql
CREATE TABLE agent_tool_calls (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    trace_id VARCHAR(64) NOT NULL,
    session_id VARCHAR(64) NOT NULL,
    run_id VARCHAR(64) DEFAULT NULL,       -- workflow 流程实例
    turn INT UNSIGNED NOT NULL,            -- 第几轮
    tool_name VARCHAR(128) NOT NULL,
    tool_call_id VARCHAR(128) DEFAULT NULL,
    arguments JSON NOT NULL,
    status VARCHAR(16) NOT NULL,           -- success/error/timeout/rejected
    result MEDIUMTEXT,
    error_message TEXT,
    duration_ms INT UNSIGNED DEFAULT 0,
    idempotency_key VARCHAR(128) DEFAULT NULL,
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_trace (trace_id),
    KEY idx_session_created (session_id, created_at),
    KEY idx_tool_created (tool_name, created_at),
    UNIQUE KEY uk_idem (idempotency_key)    -- 幂等键唯一！
) ENGINE=InnoDB;
```

**`UNIQUE KEY uk_idem (idempotency_key)` 是最关键的一个索引**——它把幂等从"应用层检查"变成"数据库层保证"。并发场景下两个请求同时写，一个成功一个撞唯一键失败，然后我们捕获这个异常返回已有结果。

⚠️ 注意：MySQL 的唯一索引允许多个 NULL，所以 `idempotency_key` 为 NULL 的行不受约束影响——这正好符合预期（没有幂等要求的调用不受限）。

### 13.5 索引设计原则

| 索引 | 用途 |
|---|---|
| `uk_session_id` | 按 session_id 精确查 |
| `idx_user_updated (user_id, updated_at)` | 列用户会话列表（复合索引，最左前缀） |
| `idx_session_created` | 时间范围查询 |
| `uk_idem` | 幂等去重 |
| `idx_tool_created` | 按工具名做聚合统计 |

**避免的索引**：
- ❌ 不要在 `content` / `result` 这类 TEXT 上建索引（除非全文检索）
- ❌ 不要给每个低区分度列单独建索引（`status`、`role`）——选择性差

### 13.6 分区

**我们没做分区**，因为数据量没到。但 Agent 场景下有两张表**迟早要分区**：

**`agent_messages`** —— 按 `created_at` 做 RANGE 分区，按月：

```sql
PARTITION BY RANGE (TO_DAYS(created_at)) (
    PARTITION p202610 VALUES LESS THAN (TO_DAYS('2026-11-01')),
    PARTITION p202611 VALUES LESS THAN (TO_DAYS('2026-12-01')),
    PARTITION pmax    VALUES LESS THAN MAXVALUE
);
```

⚠️ **分区键必须是主键的一部分**。所以主键要改成 `(id, created_at)`，或者用 MySQL 8 的**函数分区**（但函数分区不能用于唯一约束，要小心）。

**`agent_tool_calls`** —— 按 `created_at` 分区 + 定期归档。注意：**分区表的归档要先 ALTER DROP PARTITION，比 DELETE 快得多**（避免大量行的 undo log 和 binlog）。

**但我想强调一点：先做归档再考虑分区**。分区是"数据还在库里但查询更快"，归档是"数据搬走了"。Agent 的审计数据过了保留期就该搬走——只留近 3 个月的，热数据在 PG/MySQL，冷数据进对象存储或 ClickHouse。

### 13.7 我们踩过的坑

**JSONB 字段名不匹配导致全量误判**。反思教训机制里，`EvalReflector` 归因时读的字段名和 `lesson_store` 写入的不一致，导致**所有评测 case 都被判成失败**。这是我们自查发现的。

**教训**：JSONB 存数据很灵活，但**读写两端的字段名必须集中定义**。Java 侧建议用常量类或 Jackson 的 `@JsonProperty` + 统一的 DTO，不要在字符串里手写字段名。

---

## 14、大文件喂给 Agent 时，上传、切片、解析、向量化链路怎么用 Java 工程化落地？

**参考回答**：

**我们项目里这条链路是完整的，而且踩了不少坑。Java 侧的工程化映射我放在最后。**

### 14.1 完整链路

```
上传 → 哈希去重 → 解析(RAGFlow→本地兜底) → 切分(文本/表格分治) → 向量化 → 混合检索
```

**第一步：上传与去重**

```python
def _compute_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:16]

def _get_checkpoint_path(file_hash: str) -> str:
    return os.path.join(checkpoint_dir, f"{file_hash}.json")
```

**按 content hash 存进度 checkpoint** → 同一个文件重复上传不重复解析（幂等靠 hash，不靠业务逻辑）。

**文件名安全**：`_sanitize_filename` + `_safe_upload_path`（防路径穿越）。

**第二步：解析——双通道 + 降级**

```python
async def _process_document(doc_id, file_path, file_ext, filename, file_size, file_hash=None):
    try:
        parse_method = "ragflow"
        chunks = await _parse_via_ragflow(file_path, filename)
    except Exception as e:
        logger.warning(f"RAGFlow解析失败: {e}")
        parse_method = "local"          # ← 降级到本地解析器
        chunks = await _parse_file(...)
```

**为什么 RAGFlow 优先**：它的版面分析（表格、公式、多栏）能力强得多。我们自己写达不到那个水平。
**为什么还要本地兜底**：RAGFlow 是黑盒且重依赖（mysql/es/minio 三件套），它挂了文档就完全进不来。本地解析器（PyMuPDF/python-docx/pandas + PaddleOCR/Tesseract）保证**可降级**。

**第三步：切分——文本和表格分治**

- 文本：`text_splitter.py`，**heading-aware**（按标题层级切，不是按固定长度硬切），chunk **1024** 字符，重叠 **100**
- 表格：`table_processor.py`，**语义行分组**——检测分组列（`product`/`department`/`group`/`class`/`status`/`region`），按分组聚成块，组上限 **2048 字符 / 20 行**

**为什么表格不能每行一条**：单行丢失表头语义，检索到"2026-03-15"根本不知道是哪列的数据。
**为什么不能整表一条**：超出 embedding 长度限制，且一张大表一个 chunk 会稀释语义。

**关键：表头重注入**。表格转成 markdown 或自然语言描述时，表头要重复带进每个 chunk——这是我们踩过的坑。

**第四步：向量化 + 入库**

embedding 服务用 `bge-base-zh-v1.5`（768 维），**query 侧要加检索指令前缀**（BGE 系列的官方要求，这是判别式细节）。

**Milvus 插入前要过权限范围**：`doc_scopes` 表记录每个文档属于哪些 scope，检索时按用户可见 scope 过滤。

### 14.2 Java 侧的工程化映射

| 环节 | Java 技术选型 | 我们用的 |
|---|---|---|
| 上传 | `MultipartFile` + 流式落盘 | FastAPI `UploadFile` |
| 大文件 | **不要 `readAllBytes()`**——用流式 + 临时文件 | 落盘后处理 |
| 哈希去重 | `MessageDigest` 流式算 SHA-256 | `hashlib.sha256` |
| 解析（主） | 调用 RAGFlow HTTP API | 同 |
| 解析（兜底） | Apache Tika / PDFBox / POI | PyMuPDF / python-docx / pandas |
| OCR | PaddleOCR / Tess4J | PaddleOCR / Tesseract |
| 切分 | 自研 `TextSplitter`（按标题树） | 自研 |
| 表格 | EasyExcel + 自研分组 | openpyxl + 自研 |
| 向量化 | Spring AI `EmbeddingModel` | bge-base-zh |
| 入库 | Milvus Java SDK | pymilvus |

**几个 Java 工程化要点**：

**1. 大文件必须流式**：
```java
// ❌ 大文件直接读进内存
byte[] bytes = file.getBytes();

// ✅ 流式哈希 + 落盘
try (InputStream in = file.getInputStream();
     DigestInputStream dis = new DigestInputStream(in, MessageDigest.getInstance("SHA-256"))) {
    Files.copy(dis, tempPath, StandardCopyOption.REPLACE_EXISTING);
}
```

**2. 解析与向量化要解耦 + 断点续传**：
```java
// 每完成一阶段就更新 checkpoint，失败可从断点继续
checkpointService.markStage(fileHash, "PARSED", chunkCount);
```

**3. 切分必须做 embedding 长度校验**：
```java
if (tokenCount(tokenizer, chunk) > maxTokenLimit) {
    // 递归切分或降级
}
```

**4. 表格转文本要保留表头**（我们踩过的坑）：
```java
StringBuilder sb = new StringBuilder();
sb.append("表头：").append(String.join(" | ", headers)).append("\n");
for (List<String> row : group) {
    sb.append(String.join(" | ", row)).append("\n");
}
```

**5. 异步处理用线程池 + 任务表**，别在 HTTP 线程里同步解析——我们用 `asyncio.create_task` + 文件 checkpoint，Java 侧对应 `@Async` 或独立 worker。

**6. 资源释放**：OCR 模型是重资源，我们用**模块级单例**（`_PADDLE_OCR_INSTANCE`），不是每实例初始化。

---

## 15、Agent 调用敏感工具（删数据、发消息、查订单）怎么鉴权、审计和回滚？

**参考回答**：

**这个题在本项目里有一半是"已实现"、一半是"设计上清楚但还没做"——我分开说。**

### 15.1 鉴权：三层

**第一层：接口层统一鉴权**

```python
# main.py：除认证和健康检查外，所有路由都挂 JWT 校验
_protected = [Depends(verify_token)]
app.include_router(workflows.router, prefix="/api/workflows", dependencies=_protected)
```

所有 workflow 接口签名都是 `user: CurrentUser = Depends(get_current_user)`。

**第二层：数据级权限（scope）**

`roles.scope_ids` + `doc_scopes` 两张表控制文档可见范围。检索时按用户可见 scope 过滤 Milvus 查询。

**还有一个"越权兜底"机制**（我觉得挺巧）：

```python
if (not filtered_results
    and intent == "kb_query"
    and user_visible_scopes is not None
    and "all" not in (user_visible_scopes or [])
    and not denied_scope_hints):
    denied_scope_hints = await _probe_denied_scopes(state, optimized_query)
```

检索为空时，如果用户有 scope 限制，去**其他 scope 探测**有没有结果——命中就提示"该内容属于 X 部门，您暂无权限"，而不是干巴巴说"没找到"。既不泄露内容（不返回内容），又给了明确指引。

**第三层：流程内的角色卡点**

```yaml
- id: manager_approve
  type: human_approval
  approver_role: manager
  on_fail: reject
```

`approver_role` 是流程声明的**语义约束**——谁该审这个节点。⚠️ 但**目前这个字段只用于展示和 payload，实际的审批人鉴权还没接**（恢复时没有校验"当前用户是不是 manager"）。这是我要主动承认的缺口。

### 15.2 审计：我们有两套

**实时：tracer span 树**

每个工具调用是一个 span，记 name/args/output_preview/duration_ms/status/error。异步落 JSONL，前端有火焰图。

**但**：`tool_trace` 只在**内存**里，返回给前端后就没了。要长期审计需要落库（见 Q13 的 `agent_tool_calls` 表设计）。

**流程侧**：workflow 每步也产生 `SpanType.WORKFLOW_STEP` 的 span，带 `flow_id`/`step_id`/`goto`。

### 15.3 敏感操作的具体防护：以"下单"为例

**① 语义层防误触发**（两道闸）

```python
# 闸一：工具描述明确排除
"description": "提交产品订单到 ERP 待排产池。当用户明确表达下单、订货、采购、
                 要货、发货等意图时使用... 注意：查询订单状态、查库存、问价格、
                 问交期等只读请求不要使用本工具。"

# 闸二：后置拦截
if _looks_like_read_only(user_input):
    return json.dumps({"started": False, "reason": "这是查询类请求，不需要走下单流程"})
```

**② 强制人工卡点**

超过 50 吨触发信用评估；超过 200 吨直接 rule_check 拦截；`human_approval` 节点是引擎强制的，**LLM 没有跳过它的能力**。

**③ 幂等键防重复**

```python
idempotency_key = f"{run_id}:{step.id}"     # run_id:step_id
```

写 ERP 时 upsert，命中返回已有单号，**不覆盖原记录**。

**为什么必须**：LangGraph 恢复时节点**会重入**（框架的"从头重跑"语义），"同一次操作被执行两次"是常态而非异常。

**④ 通知与写库分离**

写库严格幂等；通知 best-effort——**失败了也不能回滚业务**，因为单子已经落库了。

### 15.4 回滚：分场景

**场景一：流程跑失败** —— 我们**没有实现流程级回滚**，因为：

- LangGraph 的 checkpoint 是**逐步**的，回滚到任意步需要框架支持
- 我们的流程是"写库 + 通知"这类**非事务性**操作，回滚代价极高

**实际做法是"补偿"而非"回滚"**——比如写库成功后通知失败，补偿动作是"重发通知"。

**场景二：单据写错了** —— 提供"取消/修改"工具，但**必须走人工确认**（`human_approval`）。

**场景三：批量操作做错了** —— 这需要真正的补偿事务。我的设计是：

```java
// 每个敏感操作先记录"操作日志 + 反向操作所需的数据"
agent_operations {
    id, trace_id, tool_name, target_id, 
    before_snapshot JSON,   // 变更前的数据，用于回滚
    compensating_action JSON,// 反向动作定义
    status  -- pending/committed/compensated
}
```

失败时用 `before_snapshot` 做反向操作。**这是 Saga 模式**——每步有对应的补偿动作，失败时逆序执行。

**Java 侧的正确做法**：
- **数据库操作**：同一个事务里做完（真正的事务，不需要 Saga）
- **跨系统/外部 API**：Saga + 补偿表 + 幂等
- **不可逆操作（发短信、扣款）**：先记审计日志再执行，**不假装能回滚**

### 15.5 我们主动承认的缺口

| 缺口 | 风险 | 计划 |
|---|---|---|
| `approver_role` 只做展示，未校验实际审批人身份 | 越权审批 | 接 `roles` 表校验 |
| `tool_trace` 不落库，无长期审计 | 无法追责历史操作 | 建 `agent_tool_calls` 表 |
| 流程无补偿机制 | 写库成功但后续失败需人工处理 | 加操作日志 + Saga |
| 部分历史/会话接口无鉴权，session_id 8 位可枚举 | 越权访问 | 挂 `get_current_user` + 归属校验 |

---

## 16、手册算法：实现一个简化版 Agent 工具调度器，支持注册工具、按名称执行、超时中断、失败重试和调用记录？

**参考回答**：

**这个我们项目里有一个"简化版"的原型，但生产用的是 LangGraph。我把两个都讲——先讲简化版调度器（对应题目要求），再讲生产版为什么用框架。**

### 16.1 简化版调度器：核心结构

```python
@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]          # JSON Schema
    handler: Callable                   # 同步或 async
    timeout: Optional[float] = None     # 单工具超时
    max_retries: int = 0                # 单工具重试次数
    retryable: Optional[Tuple] = None   # 哪些异常可重试
    idempotent: bool = False            # 是否幂等（决定能否安全重试）

@dataclass
class ToolCallRecord:
    call_id: str
    name: str
    arguments: Dict[str, Any]
    status: str          # success/error/timeout/retry_exhausted
    result: Any
    error: Optional[str]
    attempts: int
    duration_ms: int


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

    # ── 参数校验 ──
    def _validate(self, spec: ToolSpec, args: Dict[str, Any]) -> Optional[str]:
        schema = spec.parameters
        required = schema.get("required", [])
        for key in required:
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

    # ── 单次执行（含超时 + 重试）──
    async def _execute_once(self, spec: ToolSpec, args: Dict[str, Any]):
        start = time.monotonic()
        try:
            if inspect.iscoroutinefunction(spec.handler):
                coro = spec.handler(**args)
            else:
                coro = asyncio.to_thread(spec.handler, **args)   # 同步工具不阻塞 event loop
            result = await asyncio.wait_for(coro, timeout=spec.timeout or self.default_timeout)
            return {"status": "success", "result": result,
                    "duration_ms": int((time.monotonic() - start) * 1000)}
        except asyncio.TimeoutError:
            return {"status": "timeout",
                    "error": f"工具 {spec.name} 超时（{spec.timeout or self.default_timeout}s）",
                    "duration_ms": int((time.monotonic() - start) * 1000)}
        except Exception as e:
            return {"status": "error", "error": str(e),
                    "duration_ms": int((time.monotonic() - start) * 1000)}

    async def execute(self, name: str, args: Dict[str, Any], call_id: str = None) -> ToolCallRecord:
        call_id = call_id or new_call_id()
        spec = self.get(name)
        if spec is None:
            rec = ToolCallRecord(call_id, name, args, "error", None, f"未知工具 {name}", 0, 0)
            self._records.append(rec)
            return rec

        # 参数校验失败不重试（重试也不会变好）
        invalid = self._validate(spec, args)
        if invalid:
            rec = ToolCallRecord(call_id, name, args, "error", None, invalid, 0, 0)
            self._records.append(rec)
            return rec

        attempts = 0
        last = None
        for attempt in range(spec.max_retries + 1):
            attempts += 1
            last = await self._execute_once(spec, args)
            if last["status"] == "success":
                break
            # 只有「幂等 + 异常可重试」才重试：
            #  非幂等工具重试 = 重复副作用（可能重复建单）
            #  参数错/超时重试也不会变好，应该把错误喂回模型
            if not spec.idempotent:
                break
            if not spec.retryable:
                break
            if attempt < spec.max_retries:
                await asyncio.sleep(backoff(attempt))

        status = last["status"] if last["status"] != "error" else (
            "retry_exhausted" if attempts > 1 else "error")
        rec = ToolCallRecord(call_id, name, args, status, last.get("result"),
                             last.get("error"), attempts, last["duration_ms"])
        self._records.append(rec)
        return rec

    # ── 批量调用（一次 LLM 返回多个 tool_call）──
    async def execute_batch(self, calls: List[Dict]) -> List[ToolCallRecord]:
        # 有数据依赖时串行；独立调用可并发
        return [await self.execute(c["name"], c.get("arguments", {}), c.get("id"))
                for c in calls]

    def get_records(self) -> List[ToolCallRecord]:
        return list(self._records)
```

### 16.2 五个要求逐条对应

| 要求 | 实现 |
|---|---|
| **注册工具** | `register(ToolSpec)`，重复注册报错；schema 和 handler 打包在一起 |
| **按名称执行** | `execute(name, args)` → 先查表 → 校验参数 → 执行 → 记录 |
| **超时中断** | `asyncio.wait_for(coro, timeout)`——**真中断**；同步工具用 `asyncio.to_thread` 包一层避免阻塞 event loop |
| **失败重试** | 只对**幂等工具 + 可重试异常**重试；参数校验失败**不重试**（重试也不会变好）；指数退避 |
| **调用记录** | `ToolCallRecord` 列表，记 name/args/status/result/error/attempts/duration_ms |

### 16.3 三个关键设计取舍

**① 重试必须看 `idempotent`**

这是最容易被忽略的点。**非幂等工具重试 = 重复副作用**。`create_order` 如果第一次其实成功了但响应超时，重试就会建第二张单。

我们的做法是**幂等键而不是靠调度器判断**——工具自己保证幂等（`run_id:step_id` upsert），调度器可以放心重试。**这两层要都有**：调度器层用 `idempotent` 标记决定能不能重试，业务层用幂等键保证即使重试了也不出错。

**② 参数校验失败不重试**

模型传错了参数，重试同一个错误没有意义。应该把错误**喂回模型**让它改。区分「参数错」和「服务挂了」是重试策略的前提。

**③ 同步工具必须 `to_thread`**

直接调同步函数会阻塞整个 event loop——这正是我们项目里 BM25 踩过的坑。

### 16.4 生产版为什么用 LangGraph 而不是这个调度器

**我们项目最终用的是 LangGraph，因为需求超出了「简化版调度器」能表达的范围**：

| 需求 | 简化版够吗 | LangGraph |
|---|---|---|
| 工具注册与执行 | ✅ | ✅ |
| 超时/重试/记录 | ✅ | ✅（我们用 resilience 层） |
| **多轮 Agent 循环** | 要自己写 | ✅ 条件边 |
| **流程分支**（如证据不足就回去补检索） | ❌ | ✅ 回边 |
| **状态持久化 + 中断恢复** | ❌ | ✅ checkpointer |
| **人工卡点**（等审批，等几天） | ❌ | ✅ `interrupt()` |
| **多图并存**（RAG 图 + 流程图） | ❌ | ✅ |

**具体例子**：我们 RAG 图里有个 `evidence_judge` 节点，判断证据够不够——不够就**回边**到 `vectorize` 重新检索，最多 2 轮。这在简化版调度器里要自己写状态机。

而 workflow 图更狠——要能在任意节点 `interrupt()` 挂起，几天后从 checkpoint 恢复。

**所以我的答案是：简化版调度器是"工具执行层"，LangGraph 是"编排层"。生产上两层都要——但生产版我们没有自己写执行层，用的是 `resilience/executor.py` + `tracer` + 我们自己的分发逻辑**（见 Q5）。

### 16.5 这份实现有回归测试

上面的代码不是伪代码——它落在 `backend/tests/test_tool_dispatcher.py`（12 个用例），覆盖：

- 按名称执行 / 重复注册拦截 / 未知工具 / 未知参数 / 缺必填 / 类型不符
- 幂等工具重试到耗尽（attempts=3）
- **非幂等工具只试一次**（attempts=1，handler 只调一次）——防重复副作用
- 超时中断（wait_for 真中断，耗时符合预期）
- 批量调用保持顺序
- 调用记录完整性
- **同步工具不阻塞 event loop**（并发跑同步阻塞工具 + 异步任务，验证后者不被拖慢）

### 16.6 Java 侧的实现要点

```java
public class ToolDispatcher {
    private final Map<String, ToolSpec> tools = new ConcurrentHashMap<>();
    private final List<ToolCallRecord> records = new CopyOnWriteArrayList<>();
    private final ScheduledExecutorService timeoutScheduler = ...;  // 超时用

    public void register(ToolSpec spec) {
        if (tools.putIfAbsent(spec.name(), spec) != null) {
            throw new IllegalStateException("工具重复注册: " + spec.name());
        }
    }

    public CompletableFuture<ToolCallRecord> execute(String name, Map<String, Object> args) {
        ToolSpec spec = tools.get(name);
        if (spec == null) return failed(name, args, "未知工具 " + name);

        String invalid = validate(spec, args);
        if (invalid != null) return failed(name, args, invalid);

        CompletableFuture<ToolCallRecord> future = new CompletableFuture<>();
        // 超时用 scheduled future + completeExceptionally
        // 执行用独立线程池（不共用业务线程池）
        // 重试用 Resilience4j 的 Retry 装饰器
        return future;
    }
}
```

**Java 侧要特别注意的两点**：
1. **超时取消是协作式的**——`future.cancel(true)` 只发中断信号，任务在 `synchronized` 块或阻塞 IO 里不会立刻停。所以超时要配合**下游自己的超时**（HTTP 客户端的 readTimeout）
2. **线程池隔离**——工具执行池和 Web 容器线程池要分开，否则慢工具会拖垮 HTTP 线程池

---

## 附：这套回答背后的项目事实速查

| 事实 | 值 | 位置 |
|---|---|---|
| 后端文件数 / 代码量 | 115 py / ~1.6 万行 | — |
| 全量测试 | 183 passed | `backend/tests/` |
| RAG 图节点数 | 7 | `app/agents/graph.py` |
| 工具数 | 8 个 function calling 工具 + 5 个 workflow handler | `app/tools/agent_tools.py` |
| 工具调用最大轮次 | `max_rounds=2`（最多 3 轮 LLM 调用） | `app/agents/nodes.py` |
| 熔断配置 | 阈值 5 次失败 / 恢复 60s / 半开试探 3 次 / 统计窗口 300s | `app/resilience/circuit_breaker.py` |
| 重试退避 | 指数退避 + 0.5~1.5 倍抖动 | `app/resilience/executor.py` |
| 重试 | LLM 3 次 / RAG 3 次 / 工具 2 次（**后两者未接线**） | 同上 |
| 短期记忆 | Redis 60 条 / 24h TTL | `app/services/session_store.py` |
| 长期记忆去重 | md5 + 0.85 相似 LLM 合并 | `app/services/memory_manager.py` |
| 检索漏斗 | 每路 10 → ×2 = 20 → RRF 截 10 → 精排 5 | `app/vectorstore/hybrid_store.py` |
| 阈值 | reranker 0.10 / vector 0.40 | `app/core/config.py` |
| 切分 | chunk 1024/100；表格 2048 字符/20 行 | `app/tools/` |
| 评测并发 | 3；RAGAS 10×3 进程 | `app/evaluation/runner.py` |
| 固定流程 | 2 个流程定义，7 种节点类型 | `app/workflows/` |
| 下单幂等键 | `run_id:step_id` | `app/workflows/handlers/erp.py` |
| 排产 | `nightly_scheduling()`，**定时器未接** | `app/workflows/handlers/order_handlers.py` |

**主动承认的缺口**（面试时主动说是加分项）：
1. `agent_max_iterations` / `tool_max_retries` 配置项**未接线**
2. 重复工具调用检测**未实现**
3. 工具参数 JSON 解析失败**静默吞掉**，应改为错误喂回模型
4. `approver_role` **未做实际审批人鉴权**
5. `tool_trace` **不落库**，无长期审计
6. 流程**无补偿机制**（只做业务层幂等）
7. 长期记忆 JSONL + Milvus **双写可能失同步**
8. 部分历史/会话接口**无鉴权**，session_id 8 位可枚举
9. **定时器未接**、**真实 ERP/通知未接**
