# LangGraph RAG 知识库系统

基于 LangGraph 构建的企业级 RAG（Retrieval-Augmented Generation）知识库问答系统，支持多格式文档解析、混合检索、多轮对话记忆和自动化质量评测。

在此基础上，系统把「对话」扩展成业务办理入口：**司机入园预约**已完整落地（对话抽字段 → 上传五类证件 → 确认卡 → 本人提交 → admin 审核 → 编号 + 二维码），并沉淀为**可配置的多流程平台**——新增申请审批类流程由业务同事在后台配置发布，不改代码、不动表结构。

## 系统架构

```
┌─────────────┐     ┌──────────────────────────────────────────────────────┐
│   Frontend   │────▶│                     Backend (FastAPI)                │
│  React + TS  │     │                                                      │
│  Ant Design  │     │  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
└─────────────┘     │  │  Query    │  │ Hybrid   │  │   Generation      │  │
                    │  │  Optimize │─▶│ Retrieve │─▶│   (LLM)           │  │
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
| 语言 | Python 3.10 | `pyproject.toml` 下限 `>=3.10`，镜像 `python:3.10-slim`，依赖 `uv.lock` 锁定 |
| Web 框架 | FastAPI + Uvicorn | 异步 REST API，支持 SSE 流式响应（token 级 + 节点级双粒度） |
| Agent 编排 | LangGraph 1.2.11 | `StateGraph(RAGState)` 状态机，条件路由（知识库问答 / 通用问答） |
| LLM | OpenAI SDK（AsyncOpenAI） | OpenAI 兼容协议，默认接 DeepSeek `deepseek-v4-pro`；premium / standard 双档独立配置，支持 function calling 与 `reasoning_content` 回传 |
| Embedding | sentence-transformers | BAAI/bge-base-zh-v1.5，768 维向量 |
| 向量数据库 | Milvus 2.4 | HNSW 索引，COSINE 相似度 |
| 关键词检索 | BM25 | jieba 分词，pickle 持久化，倒排索引（自研，不依赖 ES） |
| 混合检索 | Milvus + BM25 并行 | 两路各取 top_k×2，RRF 融合去重后截 top_k |
| 重排序 | CrossEncoder | BAAI/bge-reranker-base 交叉编码器 |
| 查询优化 | 规则 + LLM | 口语化纠正、意图分类、关键词提取、LLM 改写与扩展 |
| OCR | PaddleOCR + Tesseract | 双引擎，PaddleOCR 优先，Tesseract 兜底 |
| 文档解析 | PyMuPDF / python-docx / pandas | 支持 PDF、Word、Excel、CSV、TXT、Markdown、JSON |
| 对话记忆 | Redis + Milvus + JSONL | 三层架构：短期(会话列表) + 长期(向量语义召回) + 工作记忆 |
| 反思教训 | JSONL + 语义分区注入 | LessonStore：系统级(评测归因) + 会话级(自评失败)两级，独立语义位注入 prompt |
| 固定流程 | YAML + LangGraph StateGraph | 声明式流程定义，interrupt 挂起/断点续跑，AsyncSqliteSaver 持久化 |
| 多流程平台 | PostgreSQL JSONB + 声明式元模型 | 通用五表 + `form_data JSONB`，新增流程零 DDL；11 段配置、dry-run 八项、hook 白名单三态、强制双人发布、改版灰度与兼容视图 |
| 入园预约 | 对话抽取 + 确定性校验 + 确认卡 | AI 全程只读，本人确认提交，admin 审核；13 字段 / 五类附件 / 编号 + 二维码 / 驳回重提 / 防串图 |
| 评测 | LLM Judge + 自定义指标 + RAGAS | 6 维度评分 + RAGAS 三指标(faithfulness/context_precision/context_recall)，质量门禁，自动回归检测 |
| 数据库 | PostgreSQL 15 + Redis 7 | PostgreSQL 存评测数据（asyncpg 连接池），Redis 存会话和任务状态 |
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
| 容器编排 | Docker Compose (9 服务) |
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
  │    ▼                             ┘ 截 top 10  │
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

### 入园预约办理流程（对话即入口）

```
用户在对话里说「帮我申请入园，我叫张三，手机号…」
  │
  ▼
后端权威意图路由（规则流水线 P0 句首办理 > P1 只读 > P2 通用）
  ├─ 两可句 ──▶ 0 秒下发「办理 / 咨询」候选卡，不调模型、不检索
  └─ 明确办理 ──▶ 绑定 flow_id + 已发布配置版本
  │
  ▼
通用四件套（只读）
  ├─ parse_flow_materials   从原文抽字段，标注置信度
  ├─ get_my_attachments     关联本人已上传的证件
  ├─ check_flow_rules       确定性校验（必填 / 时间 / 上限 / 附件绑定）
  └─ push_flow_confirm_card 信息齐全才生成确认卡（PII 打码）
  │
  ▼
确认卡「去表单核对并提交」──▶ 表单预填，本人逐项核对
  │
  ▼
本人提交（POST …/submit）──▶ draft → pending，写审计
  │
  ▼
admin 审核（pending → approved / rejected）
  ├─ approved ──▶ 生成通行编号 + 二维码
  └─ rejected ──▶ 本人可 reopen 修改后重提
```

**硬边界**：模型不写库、不审批、不代提交；提交动作只能由本人在表单里触发；同一账号不能既申请又审核。

## 项目结构

```
langgraph-rag/
├── backend/
│   ├── app/
│   │   ├── api/                    # API 路由层
│   │   │   ├── chat.py             # 对话接口 (同步/SSE 流式)，含 flow_* 字段与 intent_choice
│   │   │   ├── flows.py            # 多流程平台 (配置管理 + 通用申请单，36 条路由)
│   │   │   ├── entry.py            # 入园兼容接口 (15 条，转发到 flows)
│   │   │   ├── documents.py        # 文档管理 (上传/删除/查询)
│   │   │   ├── evaluation.py       # 评测接口
│   │   │   ├── workflows.py        # YAML 固定流程引擎
│   │   │   └── health.py           # 健康检查
│   │   ├── main.py                 # FastAPI 应用入口（路由注册 / 异常处理 / startup）
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
│   │   │   ├── entry_schema.py     # 入园 13 字段 / 抽取 / 确定性校验（配置导出的来源）
│   │   │   ├── entry_service.py    # 旧入园写入服务（异常类型来源）
│   │   │   ├── entry_compat.py     # 旧 /api/entry 薄兼容层 → flows/service
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
│   │   │   ├── text_splitter.py    # 文本 heading-aware 切分
│   │   │   └── entry_tools.py      # 旧入园四件套（兼容层，新链路走 flows/tools.py）
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
│   │   ├── workflows/              # 固定流程引擎（YAML 强编排）
│   │   │   ├── state.py            # WorkflowState + YAML 模型
│   │   │   ├── loader.py           # YAML 加载 + 版本管理
│   │   │   ├── nodes.py            # 通用节点实现 + 日期归一化
│   │   │   ├── engine.py           # YAML → StateGraph 编译器
│   │   │   ├── checkpointer.py     # AsyncSqliteSaver 管理
│   │   │   ├── handlers/           # 业务 handler (写库/通知) + ERP 模拟层
│   │   │   └── definitions/        # YAML 流程定义 + 新增指南
│   │   ├── flows/                  # 多流程配置化平台（申请审批类，配置即流程）
│   │   │   ├── model.py            # 11 段元模型 + FlowDefinition 派生物
│   │   │   ├── validator.py        # dry-run 八项校验（含审批链完整性）
│   │   │   ├── hooks.py            # 命名扩展点白名单 + 三态生命周期
│   │   │   ├── store.py            # 草稿/版本/双人发布/回滚/灰度/审计/快照
│   │   │   ├── registry.py         # 运行时注册表 + 后端权威意图路由
│   │   │   ├── service.py          # 通用状态机与业务写库
│   │   │   ├── tools.py            # 通用只读四件套
│   │   │   ├── context.py          # 请求级 contextvar（模型无法伪造身份）
│   │   │   ├── seed.py             # 入园配置导出 + 老三表迁移/兼容视图
│   │   │   └── template.py         # 零改动新建流程模板
│   │   ├── resilience/             # 容错机制
│   │   │   ├── circuit_breaker.py  # 熔断器
│   │   │   └── resilience_manager.py
│   │   ├── crawler/                # 法规爬虫
│   │   │   └── crawler.py          # httpx + BeautifulSoup
│   │   └── core/                   # 核心配置
│   │       ├── config.py           # 全局配置 (pydantic-settings)
│   │       ├── database.py         # PostgreSQL 连接池 + 通用五表 DDL
│   │       ├── entry_context.py    # 入园请求上下文（兼容层）
│   │       └── model_config.py     # 模型配置管理
│   ├── tests/                      # 测试（含 test_flow_platform.py 24 项）
│   ├── migrations/                 # 数据库迁移
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/
│   │   ├── pages/                  # 页面
│   │   │   ├── ChatPage.tsx        # 对话页面
│   │   │   ├── DocumentPage.tsx    # 文档管理
│   │   │   ├── EvaluationPage.tsx  # 评测面板
│   │   │   ├── RecordPage.tsx      # 调用追踪
│   │   │   ├── FlowApplyPage.tsx   # 通用申请表单（按配置渲染）
│   │   │   ├── FlowMyPage.tsx      # 通用「我的申请」
│   │   │   ├── FlowReviewPage.tsx  # 通用审核页（admin）
│   │   │   ├── FlowConfigPage.tsx  # 配置后台（模板/校验/发布/版本/灰度）
│   │   │   └── Entry*Page.tsx      # 入园旧页面（兼容壳）
│   │   ├── components/             # 组件
│   │   │   ├── Chat/               # 对话组件（消息 / 输入 / 候选卡挂载）
│   │   │   ├── Flow/               # 通用确认卡 / 选择卡 / 申请单详情
│   │   │   ├── Entry/              # 入园兼容壳（re-export Flow）
│   │   │   ├── Document/           # 文档组件
│   │   │   ├── Upload/             # 上传组件
│   │   │   └── Layout/             # 布局（动态菜单来自后端配置）
│   │   ├── stores/                 # Zustand：chatStore / flowStore / authStore
│   │   ├── api/                    # API 调用层（chat / flow / entry）
│   │   ├── types/                  # TypeScript 类型（FlowPayload / IntentChoice）
│   │   └── App.tsx                 # 应用入口 + 通用路由
│   ├── Dockerfile
│   └── package.json
├── docker/
│   └── docker-compose.yml          # 9 服务容器编排
├── k8s/                            # Kubernetes 部署配置
├── shared/
│   └── entry_intent_cases.json     # 40 条意图用例基线（已迁入 DB 配置）
├── docs/                           # 设计 / 面试 / 答辩文档（01–10）
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
| `PREMIUM_MODEL_PROVIDER` | 主力模型供应商（OpenAI 兼容） | `deepseek` |
| `PREMIUM_MODEL_NAME` | 主力模型 | `deepseek-v4-pro` |
| `PREMIUM_MODEL_API_KEY` | 主力模型 API Key | - |
| `PREMIUM_MODEL_API_BASE_URL` | 主力模型地址（**不要**带 `/anthropic`） | `https://api.deepseek.com` |
| `STANDARD_MODEL_NAME` | 标准档模型 | `deepseek-v4-pro` |
| `STANDARD_MODEL_API_KEY` | 标准档 API Key | - |
| `EMBEDDING_MODEL_NAME` | Embedding 模型 | `BAAI/bge-base-zh-v1.5` |
| `RERANKER_MODEL_NAME` | 重排序模型 | `BAAI/bge-reranker-base` |
| `EMBEDDING_DIMENSION` | 向量维度 | `768` |
| `MILVUS_HOST` / `MILVUS_PORT` | Milvus 地址 | `localhost` / `19530` |
| `POSTGRES_HOST` / `POSTGRES_PORT` | PostgreSQL 地址 | `localhost` / `5434` |
| `POSTGRES_DB` | 库名（通用五表与业务数据） | `chemical_rag` |
| `REDIS_HOST` / `REDIS_PASSWORD` | Redis 会话存储 | `localhost` / 见 `docker/.env` |
| `AUTH_ENABLED` | 是否启用 JWT 认证 | `true` |
| `JWT_SECRET_KEY` | JWT 密钥；为空时启动随机生成（重启后旧 token 失效） | - |
| `JWT_EXPIRE_MINUTES` | token 有效期 | `1440` |
| `WORKFLOW_ENABLED` | YAML 固定流程引擎开关 | `true` |

完整配置见 `.env.example`。

### 体验入园预约与多流程平台

```bash
# 1. 启动后打开前端 http://localhost:5173
# 2. admin 登录，通过 POST /api/auth/users 创建一个 driver 账号（角色 driver）
# 3. driver 登录：
#    - 侧边栏「入园申请」按表单填写并上传五类证件；或
#    - 直接在对话里说「帮我申请入园，我叫张三，手机号 138…」
# 4. 信息齐全后出确认卡 →「去表单核对并提交」→ 本人提交
# 5. admin 登录「入园审核」→ 通过后生成通行编号 + 二维码
# 6. admin 进入「流程配置」(/flows/config)：
#    新建流程 → 取模板 → dry-run → 存草稿 → 另一账号发布
#    发布后新菜单自动出现，开发者不改一行代码
```

### 测试

```bash
# 后端（含多流程平台 24 项用例）
cd backend
uv run pytest -q

# 前端：类型检查 + 单测 + 生产构建
cd frontend
npx tsc -b
npx vitest run
npm run build
```

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

### 多流程配置化平台（通用，36 条）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/flows` | 已发布流程列表 |
| GET | `/api/flows/entry-points` | 动态菜单入口（按角色过滤） |
| GET | `/api/flows/render-config` | 配置驱动渲染（字段/分组/附件/状态标签，不含 PII 实值） |
| GET | `/api/flows/template` | 新建流程的最小可发布模板 |
| GET / PUT | `/api/flows/{flow_id}/draft` | 读 / 存草稿（PUT 带 `expect_draft_revision` 乐观锁） |
| POST | `/api/flows/{flow_id}/validate` | dry-run 八项校验 |
| POST | `/api/flows/{flow_id}/publish` | 发布（强制双人，改版自动灰度） |
| POST | `/api/flows/{flow_id}/rollback` | 回滚到指定版本 |
| GET | `/api/flows/{flow_id}/versions` | 版本列表 |
| GET | `/api/flows/{flow_id}/versions/{version}/diff` | 版本差异 |
| GET / POST | `/api/flows/{flow_id}/test-cases` | 意图用例维护 |
| POST | `/api/flows/{flow_id}/test-cases/run` | 用例回归 |
| GET / POST | `/api/flows/{flow_id}/rollout` | 灰度用户组 / 账号白名单 |
| POST | `/api/flows/{flow_id}/parse` | 只读解析（不落库） |
| POST | `/api/flows/{flow_id}/form/validate` | 确定性校验（提交前） |
| POST | `/api/flows/{flow_id}/attachments` | 上传附件（类型 / 大小 / 文件头校验） |
| GET / POST | `/api/flows/{flow_id}/applications` | 我的申请列表 / 创建草稿 |
| POST | `/api/flows/{flow_id}/applications/{id}/{submit,cancel,reopen,review}` | 状态流转 |
| GET | `/api/flows/{flow_id}/applications/{id}/pass` | 通行编号 + 二维码 |
| GET | `/api/flows/{flow_id}/audit`、`…/applications/{id}/audit` | 配置审计 / 单据审计 |

### 入园预约（兼容接口，15 条）

旧前端与历史报表仍在用的薄兼容层，内部转发到通用平台，响应结构保持不变：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/entry/parse` | 只读解析原文 |
| GET | `/api/entry/meta` | 字段 / 分组 / 附件元数据 |
| POST | `/api/entry/validate` | 表单校验 |
| GET / POST | `/api/entry/applications` | 我的申请 / 创建 |
| POST | `/api/entry/applications/{id}/{submit,cancel,reopen,review}` | 状态流转 |
| GET | `/api/entry/applications/{id}/pass` | 通行证 |

> 完整 36 + 15 条路由以 `http://localhost:8000/docs` 为准，设计说明见 [docs/10-多流程配置化平台设计.md](docs/10-多流程配置化平台设计.md)。

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

槽位类型：`str` / `int` / `float` / `bool` / `date`。`date` 会自动把「11.30号」「下个月底」「下月15号」「明天」等中文相对日期归一化为 `YYYY-MM-DD`。

### 聊天里直接下单

`create_order` / `resume_order` 已注册为 function calling 工具，**不需要新前端页面**——在对话框里聊，LLM 识别出下单意图就自动走流程：

```
用户："下一个 11.30 号发货的体积 5 吨的甲烷订单"
  ↓ LLM 识别下单意图 → create_order → 抽槽位 → 缺客户则追问
  ↓ "还需要补充以下信息：customer。"
用户："客户是 X 化工厂"
  ↓ resume_order → 校验 → 写 ERP 待排产池 → 通知
  ↓ 单号 MO202610220001
```

**新增流程**：把 YAML 放进 `backend/app/workflows/definitions/`，重启自动加载。详见 [definitions/README.md](backend/app/workflows/definitions/README.md)。

**边界**：workflow 只做「接单并落库」，「等 20:00 排产」是**定时任务**（`nightly_scheduling()`）——挂起是等人不是等时间，把时间等待塞进流程会让实例在 checkpoint 里躺十几个小时。

## 入园预约与多流程配置化平台

> 完整设计（含决策记录、迁移方案、验收数据）见 [docs/10-多流程配置化平台设计.md](docs/10-多流程配置化平台设计.md)。

### 架构结论：一个内核 + 一层路由 + N 份配置

后续还会有 ERP 危化品入库 / 出库、财务、仓库、质检等流程，都从同一个 RAG 对话入口进。因此**不是**建 N 个工作流，也**不是**建 N 个 Agent，而是：

```
对话内核（单 Agent + function calling + 槽位追问 + 确认闸门）
        │
        ▼
意图路由层（后端权威：P0 句首办理 > P1 只读 > P2 通用，歧义下发候选卡）
        │  flow_id + action
        ▼
流程配置注册表（每份配置 = 11 段元模型，按 flow_id 裁剪工具与提示词）
        │
        ▼
确定性编排（校验 → 确认卡 → 本人提交 → admin 审批 → 编号/二维码）
```

- **工作流**只负责「确认之后」的确定性编排，不参与意图猜测。
- **MCP** 是未来接 ERP 的工具协议；**Skill** 是流程知识包（话术 / SOP），二者都不替代对话内核。
- **多 Agent** 暂不引入：等出现跨流程协同、独立知识域与独立权限域的真实需求再拆。

### 六个标准件

| 标准件 | 内容 | 落点 |
|---|---|---|
| `schema` | 字段 key / 类型 / 必填 / 分组 / 抽取规则 / 展示顺序 | `fields[]` |
| `rules` | 必填、类型/正则/范围、附件数量、时间窗与重量上限等确定性校验 | `fields[].validation` + `attachments[]` + `hooks` |
| `tools` | 只读四件套 `parse_flow_materials` / `get_my_attachments` / `check_flow_rules` / `push_flow_confirm_card` | `flows/tools.py`（全平台一份） |
| `submit` | 5 态骨架、允许流转子集、审批链、通行证 hook | `workflow` |
| `card` | 确认卡分组、PII 打码、表单预填目标 | `card` |
| `permissions` | 申请角色、审核角色、入口可见角色 | `permissions` + `entry_points[]` |

「四件套」是 `tools` 标准件在入园流程里的真实实现，不是与六标准件并列的另一套东西。

### 11 段配置元模型

| 段 | 作用 |
|---|---|
| `meta` | flow_id、名称、版本、描述 |
| `fields` | 字段定义（类型、必填、分组、抽取规则、校验） |
| `intent` | 规则流水线、领域词、歧义提示、用例表、分类器模式 |
| `attachments` | 附件类型、必填、数量、大小、文件头、复用策略 |
| `workflow` | 状态标签、允许流转、审批链、通行证开关 |
| `session` | 会话前缀、是否续留、切换策略、TTL、版本锁定 |
| `entry_points` | 动态菜单 / 快捷入口（label、path、可见角色） |
| `card` | 确认卡标题、分组、附件区、预填目标 |
| `prompts` | 系统提示词、话术、追问风格 |
| `hooks` | 引用的命名扩展点（只能选白名单，不能写脚本） |
| `permissions` | 申请角色、审核角色、配置角色隔离 |

### 五张通用表（新增流程零 DDL）

| 表 | 作用 |
|---|---|
| `flow_definitions` | 流程主档：当前草稿、已发布版本、状态 |
| `flow_definition_versions` | 版本快照 + diff + 发布人 / 时间 |
| `flow_applications` | 申请单主表：`form_data JSONB` + `result JSONB` + 锁定 `definition_version` |
| `flow_attachments` | 附件：显式 ID、归属人、`application_id` 绑定、复用策略 |
| `flow_audit_log` | 配置审计 + 单据流转审计（不含原始 PII） |

### 治理与发布

- **dry-run 八项**：① 元模型自校验 ② 引用一致性 ③ 扩展点白名单与参数值域 ④ 意图流水线与冲突检测 ⑤ 意图用例回归 ⑥ 入口与角色白名单 ⑦ 抽取规则与 PII 打码 ⑧ 审批链完整性。
- **审批链硬约束**：必须有 `pending → approved/rejected` 的 review 流转，actor 必须是 admin 级；禁止 `draft → approved` 直连。
- **强制双人发布**：草稿最后提交者 ≠ 发布者（bootstrap 例外）。
- **改版自动灰度**：首次发布可全量，后续按用户组 / 账号白名单放量。
- **双重版本锁定**：会话与单据都锁定 `definition_version`，配置改版不打断在途申请。
- **hook 三态**：`active → deprecated → retired`，下线前扫描引用；配置里禁止表达式 / 脚本，只允许字面量参数。
- **兼容视图**：老三表改名 `*_legacy`，同名只读视图映射到 `flow_*`，保留 90 天且跨月结 / 季度结。
- **办结收敛**：用户在该流程已有 `pending` / `approved` 单据时，会话不再靠 `session_keep` 续留，闲聊与知识问答回到普通 RAG；明确说「再申请一张」仍会进入流程。

### 新增一条流程要做什么（开发者改动文件数 = 0）

1. admin 进入 `/flows/config` → 「新建流程」，填 `flow_id`、名称、意图关键词。
2. 后台调用 `/api/flows/template` 取最小可发布模板（3 字段、完整审批链、动态菜单、确认卡）。
3. 按业务改字段 / 附件 / 审批链 / 提示词，跑 dry-run 八项。
4. 存草稿 → **另一个具备 `config_publisher` 的账号**发布。
5. 侧边栏自动出现新入口，申请人可直接对话办理、提交，admin 在 `/flows/{flow_id}/review` 审核。

### 与 YAML 固定流程（Workflow）的分工

| 场景 | YAML Workflow | `flow_*` 平台 |
|---|---|---|
| 步骤强编排、副作用明确（下单 / 排产 / 通知） | ✅ 首选 | 不适合 |
| 申请—审批类，字段 / 附件 / 审批链由业务配置 | 不适合 | ✅ 首选 |
| 新增成本 | 写 YAML（必要时写 handler） | 后台配置 + dry-run + 双人发布 |
| 状态模型 | 自定义步骤图 + checkpoint 挂起 | 固定 5 态 + 版本锁定 |

### 已知边界

- 意图分类唯一生效模式是 `rules`；`hybrid` / 外部判定模型按设计预留不接。
- 动态明细行、级联字段一期未支持；自定义组件插槽为预留。
- 纯陈述句（「我拉了一车甲醇到门口了」）不带办理动词时按知识库问答处理；应说「帮我申请入园…」或点 AI 快捷入口。
- 兼容视图到期删除、配置后台「显式切换新版」交互、各部门知识库 `scope` 归属仍需业务方确认。

## 评测体系

系统内置自动化评测，覆盖 6 个维度：

| 维度 | 权重 | 说明 |
|------|------|------|
| accuracy | 1.5 | 事实准确性 |
| completeness | 1.2 | 回答完整性 |
| relevance | 1.0 | 相关性 |
| clarity | 0.8 | 表述清晰度 |
| citation | 1.0 | 引用准确性 |
| hallucination | 1.5 | 反幻觉（权重最高） |

> 除 6 维 LLM Judge 外，另接 RAGAS 三指标（`faithfulness` / `context_precision` / `context_recall`）用于检索与忠实度层面的回归检测。

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

## 文档索引

| 文档 | 内容 |
|---|---|
| [01-技术分析.md](docs/01-技术分析.md) | 代码级技术分析与选型权衡 |
| [02-组内分享.md](docs/02-组内分享.md) | 组内分享讲稿与演示路径 |
| [03-面试准备.md](docs/03-面试准备.md) | 由浅入深的面试问答 |
| [04-固定流程设计.md](docs/04-固定流程设计.md) | YAML Workflow 引擎设计 |
| [05-Agent工程面试题对照回答.md](docs/05-Agent工程面试题对照回答.md) | Agent 工程题 × 本项目实现 |
| [06-Python面试清单.md](docs/06-Python面试清单.md) | Python 考点 × 本项目代码 |
| [07-完整面试题库.md](docs/07-完整面试题库.md) | 完整题库（唯一入口） |
| [08-文档入库与测试用例生成全流程.md](docs/08-文档入库与测试用例生成全流程.md) | 文档入库管线代码级导读 |
| [09-面试项目答辩手册.md](docs/09-面试项目答辩手册.md) | 项目答辩问答手册 |
| [10-多流程配置化平台设计.md](docs/10-多流程配置化平台设计.md) | 入园预约与多流程平台设计、迁移与验收 |

## License

Internal use only.
