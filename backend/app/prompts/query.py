"""查询优化 prompt builder（原 query_rewriter.py 内联 f-string，2026-09-25 外部化）

这些模板含 JSON 输出示例（花括号），不能用 str.format 模板常量
（会与占位符冲突），因此以 builder 函数形式外部化，函数体保持原 f-string 原样。
"""


def build_rewrite_prompt(query: str, query_type_value: str, keywords_str: str) -> str:
    """基础改写（_llm_rewrite 用，当前主链路走 build_rewrite_and_route_prompt）"""
    return f"""你是一个化工领域查询优化专家。请优化以下用户查询，使其更适合知识库检索。
原始查询：{query}
查询类型：{query_type_value}
关键词：{keywords_str}
优化要求：
1. 修正语法错误和错别字
2. 补充缺失的上下文
3. 将口语化表达转为专业表达
4. 保持原意不变
只返回优化后的查询，不要解释。"""


def build_rewrite_and_route_prompt(
    query: str, query_type_value: str, keywords_str: str, is_comparison: bool = False
) -> str:
    """改写 + 路由判断（+ 比较类子查询分解），单次 LLM 调用完成三件事"""
    prompt = f"""你是化工领域查询优化专家。优化用户查询用于知识库检索，并同时判断该问题的路由。

原始查询：{query}
查询类型：{query_type_value}
关键词：{keywords_str}

优化要求：
1. 修正语法错误和错别字
2. 补充缺失的上下文
3. 将口语化表达转为专业表达
4. 保持原意不变

路由判断（route 字段）：
- "kb_query"：答案可能存在于企业知识库文档中（人员信息、制度规范、设备工艺等）
- "general"：纯闲聊、常识、时事等与知识库无关的问题
- "business_query"：涉及订单、库存、质检、采购等业务数据查询"""

    if is_comparison:
        prompt += """

子查询分解（sub_queries 字段）：
这是一个比较类查询。请将查询分解为多个子查询，每个子查询针对一个待比较的实体，用于独立检索该实体的信息。
例如："张三、李四、王五哪个年龄最大？" → ["张三的年龄", "李四的年龄", "王五的年龄"]
如果不需要分解，返回空数组。"""

    prompt += """

只返回 JSON：{"rewritten": "...", "route": "kb_query" 或 "general", "sub_queries": [...]}"""
    return prompt


def build_expand_prompt(query: str, query_type_value: str) -> str:
    """查询扩展：生成 3 个不同角度的变体提升召回"""
    return f"""基于以下查询，生成3个不同角度的查询变体，用于提升知识库检索召回率。
原始查询：{query}
查询类型：{query_type_value}
要求：每个变体从不同角度提问，使用同义词或相关术语，保持语义一致，每行一个查询，不要编号。"""
