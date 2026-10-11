# LangGraph RAG 知识库系统

## 项目概述

基于 LangGraph 的 RAG 知识库问答系统，支持多格式文档解析、混合检索、多轮对话记忆、自动评测。

## 技术架构

- **后端**: FastAPI + LangGraph + OpenAI SDK (小米 mimo 模型)
- **前端**: React 18 + TypeScript + Ant Design 5 + Zustand
- **向量库**: Milvus 2.4 (HNSW, COSINE)
- **关键词检索**: BM25 (jieba 中文分词)
- **重排序**: CrossEncoder (bge-reranker-base, 512 token 截断 / batch 16)
- **Embedding**: BGE-base-zh-v1.5 (768维)
- **数据库**: PostgreSQL 15 (评测) + Redis 7 (会话)
- **OCR**: PaddleOCR (主) + Tesseract (备)
- **文档解析**: PyMuPDF(PDF) + python-docx(Word) + pandas(Excel/CSV)
- **部署**: Docker Compose (9服务) + K8s 配置

## 已完成的优化

### 1. 文档增量更新
- 已实现文档删除时清理 Milvus + BM25 索引
- 支持 reupload 覆盖上传
- 文档元数据 JSON 持久化

### 2. 多轮对话记忆
- 三层记忆架构: 短期(Redis List, 60条) + 长期(向量语义召回) + 工作记忆(上下文构建)
- LLM 自动从对话中提取事实/偏好/决策
- 记忆去重、过期清理、token 预算控制

### 3. 检索质量评估 (2026-08-24)
- 幻觉检测 avg 6.1 -> 9.2: content_preview 100->500字符, 新增 content 字段, prompt 放宽
- 门禁阈值: min_overall=7.0, min_hallucination=7.0, min_citation=6.0
- LLM Judge 6维度评分 (accuracy/completeness/relevance/clarity/citation/hallucination)

### 4. 文档切分策略 (2026-08-24)

**表格切分 - 语义行分组** (`tools/table_processor.py`):
- 分组键检测: 表头关键词匹配 -> 最低基数启发式 -> 放弃分组
- 按业务字段(客户/类别/订单号)分组，每组一个 chunk，带表头重复注入
- 超大组按 max_rows_per_subgroup 子切分
- 4个解析器(excel/csv/pdf/word)统一委托给 TableProcessor

**文本切分 - heading-aware** (`tools/text_splitter.py`):
- split_by_heading(): 按段落边界(\n\n/\n)切分，保留完整段落
- 超大段落退回句子边界子切分，带 overlap
- CHUNK_SIZE 512->1024, CHUNK_OVERLAP 50->100
- PDF parser 修复了 section_title 传递 (之前丢失)

### 5. API 认证机制 (2026-08-28)

**JWT 认证** (`core/security.py` + `api/auth.py`):
- 所有 `/api/*` 路由（除 auth/health）要求 `Authorization: Bearer <JWT>`
- 用户存 PostgreSQL `users` 表，bcrypt 密码哈希（直接用 bcrypt 库——passlib 1.7.4 与 bcrypt>=4.1 不兼容，hash 时直接 ValueError）
- 首次部署注册接口开放创建管理员账户，之后自动关闭
- 配置: `AUTH_ENABLED`(默认 true)、`JWT_SECRET_KEY`(.env 配置；为空则每次启动随机生成，重启后 token 失效)、`JWT_EXPIRE_MINUTES`(默认 24h)
- 评测 runner 内部回调 RAG API 使用自签服务 token (`eval_runner` 用户)
- 前端: LoginPage 登录/注册、authStore (zustand + localStorage)、axios 拦截器注入 token + 401 自动跳登录、路由守卫 RequireAuth、Sidebar 显示用户名/退出按钮

### 6. RAGAS 评测修复 (2026-08-28)
- 根因: 主进程 (uvicorn+paddle/torch) 内 import ragas 触发运行时冲突 segfault
- 修复: 评分全部在 `backend/ragas_worker.py` 子进程完成，主进程绝不 import ragas；批量样本按 chunk=10 合并单次子进程调用；超时保护 (RAGAS_TIMEOUT_SECONDS=600)；子进程崩溃/超时优雅降级为 0 分；连续失败 3 次进程内自动停用
- worker 用桩 embedding 阻止 torch/HuggingFace 加载 (三个指标均为纯 LLM 指标)
- 配置: `RAGAS_ENABLED`(默认 true)
- 已验证: evaluate_single 端到端返回真实分数 (faithfulness/context_precision/context_recall)

### 7. Milvus 表达式注入修复 (2026-08-28)
- `hybrid_store.py` 的 delete/query 过滤表达式曾用 f-string 直接拼接文件名/doc_id，含引号的恶意输入可破坏表达式
- 修复: `_milvus_escape()` 转义反斜杠和双引号，三处调用点 + memory_manager 的删除表达式均已加固

### 8. 评测性能优化 (2026-08-28)

**三阶段并发架构** (`evaluation/runner.py`)：
- 阶段1: 用例并发执行（`EVAL_CONCURRENCY=3` 信号量），单用例内幻觉/一致性/Judge 三个独立 LLM 指标 `asyncio.gather` 并发
- 阶段2: RAGAS 批量评分——先收集全部 (question, answer, contexts)，按 chunk=10 合并子进程调用，chunk 间并发（上限 3 进程）
- 阶段3: 合并 RAGAS 分数出总分
- 多轮用例的 setup_questions 保持串行（依赖前面轮次的对话上下文）

**分层抽样**：core 层始终全量（回归基线），其他层按 `EVAL_SAMPLE_RATIO=0.2` 随机抽样，固定 seed (`EVAL_SAMPLE_SEED`) 保证可复现对比；API/前端可传 `sample_ratio` 覆盖（1.0=全量）

**实测效果**：core 层 10 条用例 45.3 分钟 → 17.7 分钟（2.6x）。剩余瓶颈是 CPU reranker（占单条用例 40%+ 耗时，并发时 CPU 饱和），上 GPU 或换轻量 reranker 模型可继续提速。

### 9. Trace 链路修复 (2026-09-06)

**并发串数据** (`tracing/tracer.py`):
- `threading.local` 存 trace_id → 改 `contextvars.ContextVar`，asyncio 下按 Task 隔离，并发请求不再互相覆盖 trace_id / span 栈
- start_trace/end_trace 记录 ContextVar token 并精确 reset，上下文无残留

**span 父子嵌套**（原 `parent_span_id` 恒为 None）:
- 新增不可变 span 栈，`parent_span_id` 与 `depth` 真正生效，链路从扁平列表变树
- `api/chat.py` 加根 span（`chat_query` / `chat_query_stream`）包住 graph 调用
- `agents/nodes.py` 节点内部补子 span: query_rewrite / embed_query / hybrid_search / expanded_search / rerank_inference
- 前端 RecordPage 按 depth 缩进 + 按 start_time 排序展示层级

**火焰图组件** (`frontend/src/components/Trace/FlameGraph.tsx`):
- 接入了此前空置的 `/tracing/traces/{id}/flamegraph` 接口，RecordPage 抽屉用 Tabs 切「火焰图 / 执行步骤」，默认火焰图
- 按 depth 分行、按 start_time 计算 left/width 画条，span_type 配色 + 图例，顶部 0/25/50/75/100% 时间刻度
- 计算**自身耗时**（总耗时 - 直接子 span 耗时和），用于区分"自己慢"还是"下游慢"——reranker 这类纯推理瓶颈自身占比 100%
- 老数据兼容：无 depth 且 parent 全 null 时从父子关系推断，推断不出退化为第 0 层，不报错

**历史脏数据**（2026-08 落盘的 199 条，勿当作正确样本）:
- 33/185 条（18%）存在**平级 span 时间重叠**——LangGraph 节点串行执行不可能重叠，这正是修复前 `threading.local` 并发串扰留下的痕迹（别的请求 span 被记进本 trace）。新数据不会再出现，可据此验证修复效果。
- 3 条含超 1 小时的 span（最长 9.87 小时），疑为悬挂 span 或时钟异常。

**落盘异步化**:
- `_persist_trace` 提交到单线程池，不再同步阻塞 event loop；executor 关闭时降级为同步写，不丢数据
- 新增 `backend/tests/test_tracing.py`（5 用例：嵌套结构 / 并发隔离 / 异常标记 / 落盘 / 关闭降级）

### 10. P0 安全/一致性修复 (2026-09-25)

代码级审查（docs/01-技术分析.md §9）发现并修复三项 P0 缺陷，回归测试 `backend/tests/test_p0_fixes.py`（10 用例），全量套件 101 passed：

**① Milvus 删除表达式双重引号（删除静默失败）**:
- `_milvus_escape` 已返回带引号字面量，但 `delete_by_file`/`delete_by_doc_id`/`query_by_file`（hybrid_store.py）及 memory_manager 的删除又外包一层引号，生成 `doc_id == ""x""` 非法表达式；异常被 except 吞掉，删除静默失败 → 孤儿 chunk 残留、检索重复
- 修复: 新增 `_build_eq_expr(field, value)` 统一拼接（含回归防护注释），4 处调用点全部改用；delete_by_file/delete_by_doc_id 失败改为向上抛；documents.py 删除 API 失败时中止（500）而非继续删元数据，_process_document 的「删除失败中止重索引」保护随之真正生效

**② 熔断器 can_execute 必然死锁**:
- 持 `self._lock` 再调 `state` property 二次加同一把不可重入 Lock；resilience 一旦接入主流程即挂死请求线程
- 修复: 提取锁内 `_resolve_state()`，state property 与 can_execute 共用；同时修复 `_half_open_requests` 只清零从不递增的问题（半开限流形同虚设），can_execute 在 HALF_OPEN 真实占用配额，状态迁移时清零

**③ 幽灵用户名回落 admin（越权通道）**:
- JWT 验签通过但 users 表查无此人 → permission_service.get_user_role 兜底返回 admin + all scopes（eval_runner 服务 token 借此工作，但任何幽灵用户名同样获得全量权限）
- 修复: config 新增 `SERVICE_ACCOUNTS` 显式白名单（默认 ["eval_runner"]，授予 service 角色）；get_user_role 区分三种情形——服务账号/已注册未分配角色(向后兼容 admin+warning)/未注册(None)；get_current_user 对 None 抛 403；AUTH_ENABLED=False 保留 anonymous 显式放行；get_visible_scopes 对 None 角色返回 []（无权限）而非 None（admin 特权语义）；CurrentUser/MeResponse 默认值改最小权限

## 关键文件

| 文件 | 用途 |
|------|------|
| `backend/app/tracing/tracer.py` | 自研链路追踪（contextvar + span 嵌套树 + 异步落盘） |
| `backend/tests/test_tracing.py` | Trace 行为测试（嵌套/并发隔离/异常/落盘） |
| `backend/tests/test_p0_fixes.py` | P0 修复回归测试（表达式拼接/熔断器死锁/权限回落） |
| `frontend/src/components/Trace/FlameGraph.tsx` | 火焰图组件（含自身耗时计算） |
| `backend/app/agents/graph.py` | LangGraph RAG 工作流定义 |
| `backend/app/agents/nodes.py` | 各节点实现 (查询优化/检索/重排/生成) |
| `backend/app/tools/table_processor.py` | 表格语义行分组 |
| `backend/app/tools/text_splitter.py` | 文本 heading-aware 切分 |
| `backend/app/services/query_rewriter.py` | 查询优化 (意图分类+LLM改写+扩展) |
| `backend/app/services/memory_manager.py` | 三层对话记忆 |
| `backend/app/vectorstore/hybrid_store.py` | Milvus + BM25 混合检索 |
| `backend/app/core/security.py` | JWT 认证 + bcrypt 密码哈希 |
| `backend/app/api/auth.py` | 登录/注册/用户信息 API |
| `backend/app/evaluation/runner.py` | 评测执行器 |
| `backend/app/evaluation/gate.py` | 质量门禁 |
| `backend/app/core/config.py` | 所有配置项 |
| `backend/ragas_worker.py` | RAGAS 子进程评分 (隔离 segfault) |
| `docker/docker-compose.yml` | Docker 部署配置 |

## 待优化项

### 高优先级
1. ~~Resilience 层未接入 RAG 流程~~ ✅ 已完成（`resilience/executor.py` 统一执行器，`llm_service._create_completion` 走 execute_async 重试，`hybrid_store` 的 vector/bm25 搜索走 execute_sync + 熔断器）
2. ~~Reranker 换 bge-reranker-base~~ ✅ 已完成（现默认 base + 512 截断/batch16）；仍跑在 CPU 上，上 GPU 可继续给评测提速
3. ~~`image_processor.py` PaddleOCR 每实例重新初始化~~ ✅ 已完成（模块级 `_PADDLE_OCR_INSTANCE` 单例 + `_get_shared_paddle_ocr()`）
4. ~~无 CI，容器 venv 未装 pytest~~ ✅ 已完成（`.github/workflows/ci.yml`：backend pytest + frontend vitest + compose-config 验证；Dockerfile 多阶段 `dev` target 含 pytest）
5. ~~docker-compose 明文弱密码~~ ✅ 已完成（所有密码 `:?` 必填插值 + `docker/.env.example` 模板 + backend REDIS_PASSWORD 与 redis 服务同源）

### 中优先级
5. ~~硬编码值散落~~ ✅ 已完成（`config.py` 新增 9 个检索参数：RETRIEVAL_TOP_K/COMPARISON_MAIN_TOP_K/CONFIDENCE_* 等，`nodes.py` 6 处改读 settings）
6. ~~Prompt 硬编码在 nodes.py~~ ✅ 已完成（`app/prompts/` 包：rag.py/memory.py/query.py，nodes.py + query_rewriter + memory_manager + memory_extractor 全部改用导入）
7. ~~graph.py 用 StateGraph(dict)~~ ✅ 已完成（`StateGraph(RAGState)` + `StateGraph(AgentState)`，state.py 补齐 `_deps`/`tools_to_call`/`tool_results` 字段）
8. ~~chat.py fire-and-forget 无异常处理~~ ✅ 已完成（`_spawn_background` 辅助函数：引用集合保活 + done_callback 日志异常）
9. ~~docker-compose 绑定个人路径~~ ✅ 已完成（`${HF_CACHE_DIR:-hf_cache}` 命名卷默认值 + `docker/.env` 本地覆盖）
10. ~~前端无测试设施~~ ✅ 已完成（vitest + jsdom + @testing-library，3 个测试文件 10 用例全通过）

### 低优先级
11. evaluation.py:263 残留 localhost 硬编码
12. main.py 用已废弃的 @app.on_event，应换 lifespan
13. image_processor.py:84 临时文件异常路径泄漏
14. 依赖冗余 (PyPDF2+PyMuPDF, 双OCR引擎, 三PG驱动)
15. 首次注册后无改密/用户管理功能 (当前单用户模式)

## 已核实的历史问题（勿重复修复）

以下曾列为待优化，2026-08-28 核实均已修复：
- Milvus collection dim bug (`hybrid_store.py` 现用 `hnsw_config.dimension`)
- 长期记忆 Milvus 索引空壳 (`memory_manager.py` 已实现真实集合+检索)
- Embedding/Reranker 同步阻塞 (`embedding_service.py`/`reranker_service.py` 已用 asyncio.to_thread)
- CORS 通配符 (main.py 已用 settings.CORS_ORIGINS)
