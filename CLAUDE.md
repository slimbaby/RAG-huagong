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
- RAGAS 因 segfault 暂禁用，返回 0

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
| `backend/app/evaluation/runner.py` | 评测执行器 |
| `backend/app/evaluation/gate.py` | 质量门禁 |
| `backend/app/core/config.py` | 所有配置项 |
| `docker/docker-compose.yml` | Docker 部署配置 |

## 待优化项

### 严重
1. `hybrid_store.py:65` - Milvus 向量维度 bug, dim 用了 metric_type 字符串
2. `memory_manager.py:246` - 长期记忆 Milvus 向量索引是空壳 (stub)
3. RAGAS 评测禁用中

### 高优先级
4. Embedding/Reranker 同步调用阻塞事件循环 (应用 asyncio.to_thread)
5. PaddleOCR 每次重新实例化 (应缓存单例)
6. CORS allow_origins=["*"] 应改为用 config 中的 CORS_ORIGINS
7. Resilience 层(熔断器/超时/重试)已实现但未接入 RAG 流程

### 中优先级
8. 大量硬编码值 (top_k=5, 置信度阈值 0.30/0.40, OCR阈值 0.5 等)
9. PDF/Word parser 的 OCR 和 LLM 描述代码重复
10. Prompt 硬编码在 nodes.py 中应外部化
11. 评测 runner 硬编码 localhost
12. State 用 raw dict 而非 RAGState TypedDict

### 低优先级
13. 无 README / CI/CD / 前端测试 / 后端集成测试
14. 依赖冗余 (PyPDF2+PyMuPDF, 双OCR引擎, 三PG驱动)
15. 无认证机制 (python-jose/passlib 在依赖里但未使用)
