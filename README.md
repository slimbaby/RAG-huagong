# LangGraph RAG 知识库系统

基于 LangGraph 构建的企业级 RAG（Retrieval-Augmented Generation）知识库问答系统，支持多格式文档解析、混合检索、多轮对话记忆和自动化质量评测。

## 系统架构

```
┌─────────────┐     ┌──────────────────────────────────────────────────────┐
│   Frontend   │────▶│                     Backend (FastAPI)                │
│  React + TS  │     │                                                      │
│  Ant Design  │     │  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
└─────────────┘     │  │  Query    │  │ Hybrid   │  │   Generation      │  │
                    │  │  Optimize │─▶│ Retrieve │─▶│   (LLM mimo)      │  │
                    │  └──────────┘  └──────────┘  └───────────────────┘  │
                    │       │             │  │              │              │
                    │       ▼             ▼  ▼              ▼              │
                    │  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
                    │  │ jieba    │  │ Milvus   │  │   Memory System   │  │
                    │  │ LLM      │  │ BM25     │  │   (3-layer)       │  │
                    │  └──────────┘  └──────────┘  └───────────────────┘  │
                    └──────────────────────────────────────────────────────┘
                              │              │              │
                    ┌─────────▼──────┐ ┌─────▼─────┐ ┌─────▼─────┐
                    │   PostgreSQL   │ │   Redis   │ │  Milvus   │
                    │   (评测数据)    │ │  (会话)   │ │  (向量)   │
                    └────────────────┘ └───────────┘ └───────────┘
```

## 技术栈

### 后端

| 模块 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI + Uvicorn | 异步 REST API，支持 SSE 流式响应 |
| Agent 编排 | LangGraph | StateGraph 状态机，条件路由（知识库问答 / 通用问答） |
| LLM | OpenAI SDK | 对接小米 mimo-72b 模型，支持 function calling 和多模态 |
| Embedding | sentence-transformers | BAAI/bge-base-zh-v1.5，768 维向量 |
| 向量数据库 | Milvus 2.4 | HNSW 索引，COSINE 相似度 |
| 关键词检索 | BM25 | jieba 中分词，pickle 持久化，倒排索引 |
| 混合检索 | Milvus + BM25 并行 | 支持 RRF / 加权融合 / Max 三种融合策略 |
| 重排序 | CrossEncoder | BAAI/bge-reranker-v2-m3 交叉编码器 |
| 查询优化 | 规则 + LLM | 口语化纠正、意图分类、关键词提取、LLM 改写与扩展 |
| OCR | PaddleOCR + Tesseract | 双引擎，PaddleOCR 优先，Tesseract 兜底 |
| 文档解析 | PyMuPDF / python-docx / pandas | 支持 PDF、Word、Excel、CSV、TXT、Markdown、JSON |
| 对话记忆 | Redis + Milvus + JSONL | 三层架构：短期(会话列表) + 长期(向量语义召回) + 工作记忆 |
| 反思教训 | JSONL + 语义分区注入 | LessonStore：系统级(评测归因) + 会话级(自评失败)两级，独立语义位注入 prompt |
| 固定流程 | YAML + LangGraph StateGraph | 声明式流程定义，interrupt 挂起/断点续跑，AsyncSqliteSaver 持久化 |
| 评测 | LLM Judge + 自定义指标 | 6 维度评分，质量门禁，自动回归检测 |
| 数据库 | PostgreSQL 15 + Redis 7 | PostgreSQL 存评测数据，Redis 存会话和任务状态 |
| 容错 | 熔断器 + 超时 + 重试 | 已实现，可接入 RAG 流程 |

### 前端

| 模块 | 技术 | 说明 |
|------|------|------|
| 框架 | React 18 + TypeScript | Vite 4.5 构建 |
| UI 组件 | Ant Design 5 | 企业级 UI 组件库 |
| 状态管理 | Zustand | 轻量级状态管理 |
| 路由 | React Router v6 | 客户端路由 |
| HTTP 客户端 | Axios | 300s 超时，支持流式 |

### 部署

| 组件 | 技术 |
|------|------|
| 容器编排 | Docker Compose (8 服务) |
| K8s | Kustomize 配置，含 CronJob 定时评测 |
| 向量库 | Milvus 2.4.17 |
| 对象存储 | MinIO (RAGFlow 依赖) |
| 搜索引擎 | Elasticsearch 8.11 (RAGFlow 依赖) |

## 核心流程

### RAG 查询流程

```
用户提问
  │
  ▼
查询优化 ──────────────────────────────────────┐
  │  ├─ 口语化纠正（"咋回事" → "怎么回事"）      │
  │  ├─ 意图分类（知识库 / 通用）                │
  │  ├─ jieba 关键词提取                        │
  │  ├─ LLM 改写优化                            │
  │  └─ 查询扩展（生成 3 个变体）                │
  ▼                                             │
意图路由 ◄──────────────────────────────────────┘
  │
  ├─ 知识库查询 ──────────────────────────────────┐
  │    │                                          │
  │    ▼                                          │
  │  向量化 (BGE Embedding)                        │
  │    │                                          │
  │    ▼                                          │
  │  混合检索 ──┐                                  │
  │    │  Milvus 向量搜索 (top 20)  ──┤            │
  │    │  BM25 关键词搜索 (top 20)  ──┤ RRF 融合   │
  │    ▼                             ┘            │
  │  重排序 (CrossEncoder, top 5)                  │
  │    │                                          │
  │    ▼                                          │
  │  置信度检查 ──▶ 不足 → 通用问答               │
  │    │                                          │
  │    ▼                                          │
  │  上下文构建 (引用标注 + 记忆注入 + 教训注入)    │
  │    │                                          │
  └────┼──────────────────────────────────────────┘
       ▼
  LLM 生成回答
       │
       ▼
  返回答案 + 引用来源
```

### 文档处理流程

```
文件上传
  │
  ▼
格式分发
  ├─ PDF ──▶ PyMuPDF 解析 + PaddleOCR (扫描件)
  ├─ Word ──▶ python-docx (段落/标题/表格/图片)
  ├─ Excel ──▶ pandas (多 Sheet)
  ├─ CSV ──▶ pandas + chardet 编码检测
  ├─ TXT/MD ──▶ 逐行解析 + Markdown 标题检测
  └─ JSON ──▶ 递归提取 key-value
       │
       ▼
  结构化元素 (heading / paragraph / table / image)
       │
       ▼
  表格切分 ──▶ 语义行分组 (按客户/类别/订单号)
  文本切分 ──▶ heading-aware 段落切分 + 句子子切分
       │
       ▼
  Embedding 向量化
       │
       ▼
  写入 Milvus + BM25 索引
       │
       ▼
  自动生成评测用例
```

## 项目结构

```
langgraph-rag/
├── backend/
│   ├── app/
│   │   ├── api/                    # API 路由层
│   │   │   ├── chat.py             # 对话接口 (同步/SSE 流式)
│   │   │   ├── documents.py        # 文档管理 (上传/删除/查询)
│   │   │   ├── evaluation.py       # 评测接口
│   │   │   ├── health.py           # 健康检查
│   │   │   └── main.py             # FastAPI 应用入口
│   │   ├── agents/                 # LangGraph Agent
│   │   │   ├── graph.py            # RAG/Agent 工作流定义
│   │   │   ├── nodes.py            # 各节点实现
│   │   │   └── state.py            # 状态类型定义
│   │   ├── services/               # 核心服务
│   │   │   ├── llm_service.py      # LLM 调用封装
│   │   │   ├── embedding_service.py# Embedding 服务
│   │   │   ├── reranker_service.py # 重排序服务
│   │   │   ├── query_rewriter.py   # 查询优化
│   │   │   ├── citation_service.py # 引用标注
│   │   │   ├── memory_manager.py   # 三层记忆系统
│   │   │   ├── memory_extractor.py # 记忆提取
│   │   │   ├── lesson_store.py     # 反思教训存储 (系统级/会话级)
│   │   │   ├── session_store.py    # Redis 会话存储
│   │   │   └── ragflow_client.py   # RAGFlow 集成
│   │   ├── prompts/                 # 集中管理的 prompt 模板
│   │   │   ├── rag.py               # RAG 三套 prompt (知识库/通用/比较)
│   │   │   ├── memory.py            # 记忆注入模板 + PromptTemplate 向后兼容
│   │   │   └── lesson.py            # 教训提炼/蒸馏/归因 prompt
│   │   ├── tools/                  # 文档解析工具
│   │   │   ├── pdf_parser.py       # PDF 解析 (PyMuPDF + OCR)
│   │   │   ├── word_parser.py      # Word 解析
│   │   │   ├── excel_parser.py     # Excel 解析
│   │   │   ├── csv_parser.py       # CSV 解析
│   │   │   ├── text_parser.py      # TXT/Markdown 解析
│   │   │   ├── json_parser.py      # JSON 解析
│   │   │   ├── table_processor.py  # 表格语义行分组
│   │   │   └── text_splitter.py    # 文本 heading-aware 切分
│   │   ├── vectorstore/            # 向量存储
│   │   │   ├── hybrid_store.py     # Milvus + BM25 混合检索
│   │   │   ├── bm25_index.py       # BM25 倒排索引
│   │   │   └── hnsw_index.py       # HNSW 配置
│   │   ├── evaluation/             # 评测系统
│   │   │   ├── runner.py           # 评测执行器
│   │   │   ├── metrics.py          # 自定义指标 (幻觉/引用)
│   │   │   ├── gate.py             # 质量门禁
│   │   │   ├── llm_judge.py        # LLM 评审
│   │   │   ├── dataset.py          # 评测数据集管理
│   │   │   ├── case_generator.py   # 自动用例生成
│   │   │   ├── reflector.py        # 评测反思归因 → 系统级教训
│   │   │   └── report.py           # 评测报告
│   │   ├── tracing/                # 全链路追踪
│   │   │   └── tracer.py           # Span 级追踪
│   │   ├── workflows/              # 固定流程引擎
│   │   │   ├── state.py            # WorkflowState + YAML 模型
│   │   │   ├── loader.py           # YAML 加载 + 版本管理
│   │   │   ├── nodes.py            # 通用节点实现
│   │   │   ├── engine.py           # YAML → StateGraph 编译器
│   │   │   ├── checkpointer.py     # AsyncSqliteSaver 管理
│   │   │   ├── handlers/           # 业务 handler (写库/通知)
│   │   │   └── definitions/        # YAML 流程定义
│   │   ├── resilience/             # 容错机制
│   │   │   ├── circuit_breaker.py  # 熔断器
│   │   │   └── resilience_manager.py
│   │   ├── crawler/                # 法规爬虫
│   │   │   └── crawler.py          # httpx + BeautifulSoup
│   │   └── core/                   # 核心配置
│   │       ├── config.py           # 全局配置 (pydantic-settings)
│   │       ├── database.py         # PostgreSQL 连接池
│   │       └── model_config.py     # 模型配置管理
│   ├── tests/                      # 测试
│   ├── migrations/                 # 数据库迁移
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/                  # 页面
│   │   │   ├── ChatPage.tsx        # 对话页面
│   │   │   ├── DocumentPage.tsx    # 文档管理
│   │   │   ├── EvaluationPage.tsx  # 评测面板
│   │   │   └── RecordPage.tsx      # 调用追踪
│   │   ├── components/             # 组件
│   │   │   ├── Chat/               # 对话组件
│   │   │   ├── Document/           # 文档组件
│   │   │   ├── Upload/             # 上传组件
│   │   │   └── Layout/             # 布局 (侧边栏)
│   │   ├── api/                    # API 调用层
│   │   ├── types/                  # TypeScript 类型
│   │   └── App.tsx                 # 应用入口
│   ├── Dockerfile
│   └── package.json
├── docker/
│   └── docker-compose.yml          # 8 服务容器编排
├── k8s/                            # Kubernetes 部署配置
├── config/
│   └── resilience.yaml             # 容错配置
└── CLAUDE.md                       # 项目记忆文件
```

## 快速开始

### 环境要求

- Docker & Docker Compose
- Git

### 启动

```bash
# 克隆项目
git clone <repo-url>
cd langgraph-rag

# 配置环境变量（后端应用配置：LLM API Key 等）
cp .env.example .env
# 编辑 .env 填入 LLM API Key 等配置

# 配置 compose 插值变量（数据库密码等 secrets，均为必填）
cp docker/.env.example docker/.env
# 编辑 docker/.env 填入强密码（注意：compose 只读 docker/.env，根目录 .env 对插值无效）

# 启动所有服务
cd docker
docker compose up -d

# 访问
# 前端: http://localhost:5173
# 后端 API: http://localhost:8000
# API 文档: http://localhost:8000/docs
# RAGFlow: http://localhost:9380
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `PREMIUM_MODEL_API_KEY` | LLM API Key | - |
| `PREMIUM_MODEL_API_BASE_URL` | LLM API 地址 | `https://api.xiaomi.com/v1` |
| `PREMIUM_MODEL_NAME` | 模型名称 | `mimo-72b` |
| `EMBEDDING_MODEL_NAME` | Embedding 模型 | `BAAI/bge-base-zh-v1.5` |
| `EMBEDDING_DIMENSION` | 向量维度 | `768` |
| `MILVUS_HOST` | Milvus 地址 | `localhost` |
| `POSTGRES_HOST` | PostgreSQL 地址 | `localhost` |
| `REDIS_HOST` | Redis 地址 | `localhost` |

完整配置见 `.env.example`。

### 依赖管理

依赖版本由 `backend/uv.lock` 锁定（Docker 构建用 `uv sync --frozen`），**改完 `pyproject.toml` 必须重新生成锁文件**：

```bash
cd backend
uv lock                    # 补齐/更新 uv.lock
uv sync --frozen --extra dev   # 按锁文件安装
```

> ⚠️ `uv.lock` 过期于 `pyproject.toml` 会导致生产镜像缺包——`uv sync --no-dev` 装出的环境在 `import` 阶段就崩。CI 建议加 `uv lock --check` 校验。

## API 接口

### 对话

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/query` | 同步 RAG 查询 |
| POST | `/api/chat/query/stream` | SSE 流式查询 |
| GET | `/api/chat/history/{session_id}` | 获取对话历史 |
| DELETE | `/api/chat/history/{session_id}` | 清除对话历史 |
| GET | `/api/chat/sessions` | 列出会话 |

### 文档管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/documents/upload` | 上传文档 |
| GET | `/api/documents/` | 列出文档 |
| GET | `/api/documents/{doc_id}` | 文档详情 |
| DELETE | `/api/documents/{doc_id}` | 删除文档 |
| GET | `/api/documents/{doc_id}/chunks` | 查看切分结果 |
| GET | `/api/documents/{doc_id}/progress` | 处理进度 |

### 评测

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/eval/run` | 启动评测 |
| GET | `/api/eval/status/{task_id}` | 评测状态 |
| GET | `/api/eval/reports` | 评测报告列表 |
| GET | `/api/eval/report/{eval_id}` | 单份报告详情 |
| POST | `/api/eval/compare` | 报告对比 |

### 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/tracing/records` | 调用追踪记录 |
| GET | `/docs` | Swagger API 文档 |

### 固定流程

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/workflows/flows` | 列出流程定义 |
| GET | `/api/workflows/flows/{flow_id}` | 流程定义详情 |
| POST | `/api/workflows/start` | 启动流程 |
| POST | `/api/workflows/runs/{run_id}/resume` | 恢复（审批 / 补信息） |
| GET | `/api/workflows/runs/{run_id}` | 查询实例状态 |
| GET | `/api/workflows/runs` | 列出当前用户实例 |
| GET | `/api/checkpoints/threads` | 列出 checkpoint 线程 |

详见 [docs/04-固定流程设计.md](docs/04-固定流程设计.md)。

## 固定流程（Workflow）

支持把"公司固定流程"（采购下单、生产工单、审批流等）接入系统。核心特性：

- **声明式定义**：流程写在 YAML 里，新增流程不改代码
- **有状态可挂起**：`interrupt()` 挂起等人审批，`Command(resume=...)` 断点续跑，前面的步骤不重跑
- **持久化**：LangGraph 官方 `AsyncSqliteSaver`，状态在 checkpoint 里而非内存，进程重启也能恢复
- **能力复用**：LLM / 检索 / 权限 / tracer / 教训全部复用 RAG 侧单例

```yaml
id: purchase_order
version: 1
name: 采购下单流程
slots:
  - {name: material_name, type: str, required: true}
  - {name: quantity, type: int, required: true}
steps:
  - {id: collect, type: slot_extract}
  - {id: check, type: rule_check, rules: [{field: quantity, op: lte, value: 5000, error: 超限}], on_fail: reject}
  - {id: approve, type: human_approval, approver_role: manager, on_fail: reject}
  - {id: write_order, type: side_effect, handler: purchase.write_order}
  - {id: notify, type: notify, handler: purchase.notify}
  - {id: done, type: end}
  - {id: reject, type: end}
```

节点类型：`slot_extract` / `rule_check` / `branch` / `human_approval` / `llm` / `side_effect` / `notify` / `end`。其中只有 `side_effect` 与 `notify` 需要写 Python，其余由引擎通用实现。

## 评测体系

系统内置自动化评测，覆盖 6 个维度：

| 维度 | 权重 | 说明 |
|------|------|------|
| accuracy | 1.0 | 事实准确性 |
| completeness | 1.0 | 回答完整性 |
| relevance | 1.0 | 相关性 |
| clarity | 1.0 | 表述清晰度 |
| citation | 1.0 | 引用准确性 |
| hallucination | 1.5 | 反幻觉（权重最高） |

质量门禁阈值：overall >= 7.0, hallucination >= 7.0, citation >= 6.0。

## 反思教训机制

把「踩过的坑」沉淀成可复用的规则，避免同类问题重复犯错。与三层记忆**解耦独立**（生命周期、机制、语义位都不同）。

### 两级教训

| 级别 | scope | 来源 | 注入方式 |
|------|-------|------|---------|
| 系统级 | `__system__` | 评测跑完，低分 case 自动归因（`EvalReflector`） | 每次请求无条件带上（上限 10 条） |
| 会话级 | 真实 session_id | 在线自评失败时提炼 | 按会话召回（上限 5 条） |

### 语义位隔离（关键设计）

教训是**行为准则**，不是事实依据。在 system prompt 中独立成段，与长期记忆、参考资料严格分开：

```
## 历史教训（同类问题的注意事项）   ← 行为准则
## 长期记忆                          ← 背景信息
## 参考资料                          ← 事实依据（答案内容只能来自这里）
```

prompt 里显式约束「历史教训是行为准则，不是事实依据，不要把它当作知识库内容引用」，避免 LLM 把教训当依据转述。

### 防护机制

| 机制 | 说明 |
|------|------|
| 置信度门槛 | `LESSON_MIN_CONFIDENCE=0.5`，低于此值不写入（防错误教训自我强化） |
| 来源标记 | `[系统级·召回]` / `[本次会话·召回]`，LLM 遇冲突可自行权衡 |
| 上限淘汰 | 系统级超上限按 `confidence × importance` 淘汰最弱者 |
| 独立开关 | `LESSON_INJECTION_ENABLED` / `_SYSTEM` / `_SESSION` 三级开关，可一键回退 |
| 原子落盘 | 写 `.tmp` + `os.replace`；加载时逐行容错，单行损坏不影响其余记录 |

### 配置项

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LESSON_INJECTION_ENABLED` | `True` | 教训注入总开关 |
| `LESSON_INJECTION_SYSTEM` | `True` | 系统级教训注入 |
| `LESSON_INJECTION_SESSION` | `True` | 会话级教训注入 |
| `LESSON_MAX_SYSTEM` | `10` | 系统级教训上限 |
| `LESSON_MAX_SESSION` | `5` | 单会话注入上限 |
| `LESSON_MIN_CONFIDENCE` | `0.5` | 置信度门槛 |
| `LESSON_STORAGE_PATH` | `./lessons` | 持久化目录 |

## 支持的文档格式

| 格式 | 解析方式 | 特殊处理 |
|------|----------|----------|
| PDF | PyMuPDF 文本提取 | 扫描件自动 PaddleOCR，表格自动检测 |
| Word (.docx) | python-docx | 标题/段落/文本框/表格/图片分别处理 |
| Excel (.xlsx/.xls) | pandas | 多 Sheet 支持，表格语义行分组 |
| CSV | pandas + chardet | 自动编码检测 (UTF-8/GBK/GB2312/GB18030) |
| TXT | 逐行解析 | 自动编码检测 |
| Markdown (.md) | 逐行解析 | `#` 标题自动识别 |
| JSON | 递归提取 | 嵌套结构展平为 key-value |

## License

Internal use only.
