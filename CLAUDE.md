# LangGraph RAG 知识库系统

## 项目概述

基于 LangGraph 的 RAG 知识库问答系统，支持多格式文档解析、混合检索、多轮对话记忆、自动评测。

## 技术架构

- **后端**: FastAPI + LangGraph + OpenAI SDK (小米 mimo 模型)
- **前端**: React 18 + TypeScript + Ant Design 5 + Zustand
- **向量库**: Milvus 2.4 (HNSW, COSINE)
- **关键词检索**: BM25 (jieba 中文分词)
- **重排序**: CrossEncoder (bge-reranker-v2-m3)
- **Embedding**: BGE-base-zh-v1.5 (768维)
- **数据库**: PostgreSQL 15 (评测) + Redis 7 (会话)
- **OCR**: PaddleOCR (主) + Tesseract (备)
- **文档解析**: PyMuPDF(PDF) + python-docx(Word) + pandas(Excel/CSV)
- **部署**: Docker Compose (8服务) + K8s 配置

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

## 关键文件

| 文件 | 用途 |
|------|------|
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
1. Resilience 层(熔断器/超时/重试, `app/resilience/`)已实现但未接入 RAG 流程，LLM/Milvus 故障会无保护级联
2. Reranker (bge-reranker-v2-m3, 568M) 跑在 CPU 上，单次精排 17-71s，拖慢线上问答和评测——上 GPU 或换 bge-reranker-base
3. `image_processor.py:19-24` PaddleOCR 每实例重新初始化 (pdf_parser 已是单例，这里漏了)
4. 无 CI，且容器 venv 未装 pytest，测试从不自动运行
5. docker-compose 明文弱密码 (backend 环境块 REDIS_PASSWORD=infiniflow 等)

### 中优先级
5. 硬编码值散落 (nodes.py:419 top_k=5、451/456 置信度阈值 0.10/0.40)
6. Prompt 硬编码在 nodes.py:183-215，应外部化管理
7. graph.py:152,191 用 StateGraph(dict) 而非 RAGState TypedDict，无状态校验
8. chat.py:305 fire-and-forget 记忆提取任务无异常处理
9. docker-compose 绑定开发者个人路径 `C:\Users\admin\.cache\huggingface`，不可移植
10. 前端无测试设施 (无 jest/vitest)

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
