"""
Prompt 集中管理

此前 prompt 散落在 agents/nodes.py、services/memory_manager.py、
services/memory_extractor.py、services/query_rewriter.py 四处硬编码，
调优/审查时需要跨文件翻找。统一收拢到本包：

- prompts.rag    RAG 生成链路的三套回答模板（知识库引用 / 通用兜底 / 比较类）
- prompts.memory 记忆系统模板（工作记忆 system prompt / 提取 / 合并）
- prompts.query  查询优化模板（改写 / 改写+路由+分解 / 扩展）——f-string 类
                 模板以 builder 函数形式提供，避免 .format 与 JSON 花括号冲突

约定：模板文本改动会直接影响回答质量与评测分数，
修改后必须跑评测分层抽样对比（core 层全量），见 evaluation/runner.py。
"""
