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
  │  上下文构建 (引用标注 + 记忆注入)              │
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
│   │   │   ├── session_store.py    # Redis 会话存储
│   │   │   └── ragflow_client.py   # RAGFlow 集成
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
│   │   │   └── report.py           # 评测报告
│   │   ├── tracing/                # 全链路追踪
│   │   │   ├── tracer.py           # Span 级追踪
│   │   │   └── checkpointer.py     # 任务检查点
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
