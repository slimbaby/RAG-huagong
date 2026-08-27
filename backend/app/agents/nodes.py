"""
LangGraph节点函数 - RAG流程的各个步骤
"""

from typing import Dict, Any, List, Optional
import json
import logging
import re
import uuid

logger = logging.getLogger(__name__)

try:
    from ..tools.agent_tools import TOOLS_SCHEMA
except ImportError:
    TOOLS_SCHEMA = []

# 问题词停用表：从查询中剔除后，剩余分词视为实体候选（人名、机构名等）
_QUESTION_WORDS = {
    "来自", "哪里", "哪儿", "什么", "怎么", "怎样", "如何", "为什么", "是谁",
    "哪位", "哪个", "哪些", "多少", "几岁", "多大", "属于", "请问", "告诉",
    "介绍", "一下", "关于", "情况", "信息", "详细", "相关", "说明",
}


def _extract_entity_candidates(text: str) -> List[str]:
    """从用户查询中提取实体候选词，用于实体精确命中兜底判断。"""
    if not text:
        return []
    try:
        import jieba
        tokens = jieba.lcut(text)
    except Exception:
        tokens = re.findall(r"[一-龥A-Za-z0-9]{2,}", text)
    return [
        t for t in tokens
        if len(t) >= 2 and t not in _QUESTION_WORDS and not t.startswith("哪")
    ]


async def query_optimize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点1: 查询优化 - 清洗、口语转换、意图识别、关键词提取、LLM改写、查询扩展"""
    query_rewriter = state.get("_deps", {}).get("query_rewriter")
    tracer = state.get("_deps", {}).get("tracer")
    user_query = state.get("user_query", "")

    logger.info(f"[query_optimize_node] user_query='{user_query}', query_rewriter={'有' if query_rewriter else '无'}")

    if not query_rewriter:
        return {
            "optimized_query": user_query,
            "query_type": "factual",
            "keywords": [],
            "expanded_queries": [],
        }

    try:
        optimized = await query_rewriter.optimize(user_query)
        logger.info(f"[query_optimize_node] optimized.rewritten='{optimized.rewritten}', intent={optimized.intent}")

        # 记录token用量
        if tracer and optimized.usage:
            tracer.record_tokens(
                optimized.usage.get("prompt_tokens", 0),
                optimized.usage.get("completion_tokens", 0),
            )

        return {
            "optimized_query": optimized.rewritten,
            "query_type": optimized.query_type.value,
            "intent": optimized.intent,
            "keywords": optimized.keywords,
            "expanded_queries": optimized.expanded_queries or [],
        }
    except Exception as e:
        logger.error(f"Query optimization failed: {e}")
        return {
            "optimized_query": user_query,
            "query_type": "factual",
            "intent": "kb_query",
            "keywords": [],
            "expanded_queries": [],
            "error": f"查询优化失败: {str(e)}",
        }


async def vectorize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点2: 查询向量化"""
    embedding_service = state.get("_deps", {}).get("embedding_service")
    optimized_query = state.get("optimized_query") or state.get("user_query", "")

    if not embedding_service:
        return {"query_vector": None}

    try:
        vector = await embedding_service.embed_query(optimized_query)
        return {"query_vector": vector}
    except Exception as e:
        logger.error(f"Vectorization failed: {e}")
        return {"query_vector": None, "error": f"向量化失败: {str(e)}"}


async def retrieval_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点3: 混合检索 - 向量检索 + BM25检索"""
    hybrid_store = state.get("_deps", {}).get("hybrid_store")
    embedding_service = state.get("_deps", {}).get("embedding_service")
    # 直接从state获取，确保不会丢失用户原始问题
    user_query = state.get("user_query", "")
    optimized_query = state.get("optimized_query") or user_query
    query_vector = state.get("query_vector")
    expanded_queries = state.get("expanded_queries", [])

    logger.info(f"[retrieval_node] state keys: {list(state.keys())}, user_query='{user_query}', optimized_query='{optimized_query}'")

    logger.info(f"[retrieval_node] optimized_query='{optimized_query}', query_vector={'有' if query_vector else '无'}, expanded_queries={len(expanded_queries)}")

    if not hybrid_store:
        logger.warning("[retrieval_node] hybrid_store为空，跳过检索")
        return {"raw_results": [], "filtered_results": []}

    try:
        results = await hybrid_store.hybrid_search(
            query=optimized_query,
            query_vector=query_vector,
            top_k=20
        )
        logger.info(f"[retrieval_node] hybrid_search返回 {len(results)} 条结果")

        # If not enough results, try expanded queries
        if len(results) < 3 and expanded_queries and embedding_service:
            for eq in expanded_queries[:2]:
                eq_vector = await embedding_service.embed_query(eq)
                eq_results = await hybrid_store.hybrid_search(
                    query=eq, query_vector=eq_vector, top_k=5
                )
                results.extend(eq_results)

        # Deduplicate
        seen_ids = set()
        unique_results = []
        for r in results:
            cid = r.get("chunk_id", "") if isinstance(r, dict) else getattr(r, "chunk_id", "")
            if cid not in seen_ids:
                seen_ids.add(cid)
                unique_results.append(r)

        # Convert to dicts（保留全部，交给 reranker 精排）
        result_dicts = []
        for r in unique_results:
            if isinstance(r, dict):
                result_dicts.append(r)
            else:
                result_dicts.append({
                    "chunk_id": r.chunk_id,
                    "content": r.content,
                    "metadata": r.metadata,
                    "vector_score": r.vector_score,
                    "bm25_score": r.bm25_score,
                    "hybrid_score": r.hybrid_score,
                    "rank": r.rank,
                })

        # 迭代补检索时，合并前序轮次的结果（reranker 会对合并后集合统一重排）
        accumulated = state.get("accumulated_results") or []
        if accumulated:
            seen_acc = {d.get("chunk_id", "") for d in result_dicts}
            added = 0
            for r in accumulated:
                cid = r.get("chunk_id", "")
                if cid and cid not in seen_acc:
                    result_dicts.append(r)
                    seen_acc.add(cid)
                    added += 1
            logger.info(f"[retrieval_node] 合并前序结果: +{added} 条，总计 {len(result_dicts)} 条")

        return {"raw_results": result_dicts, "filtered_results": result_dicts}

    except Exception as e:
        logger.error(f"Retrieval failed: {e}")
        return {"raw_results": [], "filtered_results": [], "error": f"检索失败: {str(e)}"}


RAG_PROMPT_WITH_CITATION_AND_TIME = """你是化工园区知识库助手。基于以下参考资料回答问题。

## 回答要求
1. 基于参考资料回答，不要编造信息
2. 在回答中必须标注引用来源，格式为 [来源X]
3. 引用格式：[来源N: 文件名, 第M页]
4. **对于法律法规类引用，必须注明发布/生效日期和知识库更新时间**
5. 如果引用的法规有多个版本，说明使用的是最新版本
6. 如果参考资料中没有相关信息，明确说明"知识库中未找到相关信息"

## 参考资料
{context}

## 问题
{question}

## 回答
"""

GENERAL_PROMPT = """你是化工园区知识库助手。用户的问题不涉及知识库文档内容，请用你的通用知识回答。

## 回答要求
1. 准确、简洁地回答问题
2. 如果不确定，请如实说明
3. **绝对禁止编造数据**：不要编造年龄、日期、数字等具体数据，如果不知道就明确说"无法确定"
4. **禁止伪造来源**：不要说"根据XX文档/表格"如果该数据实际上不在文档中
5. 在回答末尾注明：「以上内容来自通用知识，非知识库文档」

## 问题
{question}

## 回答
"""


async def _chat_with_tools(llm_client, messages, tools_schema, tool_registry, max_rounds=2):
    """带工具调用循环的对话。返回 {"text", "usage", "tool_trace": [...]}"""
    tool_trace = []
    msgs = list(messages)
    usage_total = {}
    for _ in range(max_rounds + 1):
        result = await llm_client.chat_with_tools(msgs, tools=tools_schema)
        for k in ("prompt_tokens", "completion_tokens"):
            usage_total[k] = usage_total.get(k, 0) + result.get("usage", {}).get(k, 0)
        calls = result.get("tool_calls") or []
        if not calls:
            return {"text": result.get("text", ""), "usage": usage_total, "tool_trace": tool_trace}
        assistant_msg = {"role": "assistant", "content": result.get("text") or "", "tool_calls": calls}
        msgs.append(assistant_msg)
        for tc in calls:
            fn_name = tc.get("function", {}).get("name", "")
            raw_args = tc.get("function", {}).get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except Exception:
                args = {}
            fn = tool_registry.get(fn_name)
            if fn is None:
                output = json.dumps({"error": f"未知工具 {fn_name}"}, ensure_ascii=False)
            else:
                try:
                    output = fn(**args)
                except Exception as e:
                    output = json.dumps({"error": str(e)}, ensure_ascii=False)
            logger.info(f"[tool_loop] {fn_name}({args}) -> {str(output)[:120]}")
            tool_trace.append({"name": fn_name, "args": args, "output_preview": str(output)[:200]})
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": output})
    # 轮次用尽，最后一次强制无工具回答
    result = await llm_client.chat(msgs)
    return {"text": result["text"], "usage": usage_total, "tool_trace": tool_trace}


async def generate_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点4: 生成回答（支持多轮记忆 + 工具调用循环）"""
    deps = state.get("_deps", {})
    llm_client = deps.get("llm_client")
    citation_service = deps.get("citation_service")
    memory_manager = deps.get("memory_manager")
    optimized_query = state.get("optimized_query") or state.get("user_query", "")
    filtered_results = state.get("filtered_results", [])
    intent = state.get("intent", "kb_query")
    retrieval_confident = state.get("retrieval_confident", True)
    session_id = state.get("session_id", "")
    tracer = deps.get("tracer")
    tool_registry = deps.get("tool_registry")

    if not llm_client:
        return {
            "answer": "LLM服务未配置，无法生成回答。",
            "citations": [],
        }

    try:
        use_general = (intent == "general") or (not retrieval_confident)

        if use_general:
            kb_context = ""
            logger.info(f"[generate_node] 使用通用问答模式, intent={intent}, retrieval_confident={retrieval_confident}")
        else:
            if citation_service:
                kb_context = citation_service.format_context_with_citation_and_time(filtered_results)
            else:
                kb_context = "\n\n".join([r.get("content", "") for r in filtered_results])
            logger.info(f"[generate_node] optimized_query='{optimized_query}', filtered_results={len(filtered_results)}条, context长度={len(kb_context)}")

        if memory_manager and session_id:
            messages, relevant_memories = await memory_manager.build_context(
                session_id=session_id,
                user_query=optimized_query,
                kb_context=kb_context,
                intent=intent,
                retrieval_confident=retrieval_confident,
            )
            logger.info(f"[generate_node] 使用记忆模式, messages={len(messages)}条, 长期记忆={len(relevant_memories)}条")
            if tool_registry:
                result = await _chat_with_tools(llm_client, messages, TOOLS_SCHEMA, tool_registry)
            else:
                result = await llm_client.chat(messages)
        else:
            if use_general:
                prompt = GENERAL_PROMPT.format(question=optimized_query)
            else:
                prompt = RAG_PROMPT_WITH_CITATION_AND_TIME.format(
                    context=kb_context, question=optimized_query
                )
            logger.info(f"[generate_node] 使用单轮模式（无记忆系统）")
            if tool_registry:
                messages = [{"role": "user", "content": prompt}]
                result = await _chat_with_tools(llm_client, messages, TOOLS_SCHEMA, tool_registry)
            else:
                result = await llm_client.generate(prompt)

        response_text = result["text"]

        usage = result.get("usage", {})
        if tracer:
            tracer.record_tokens(
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )

        # Parse citations
        if citation_service:
            citations = citation_service.parse_citations(response_text, filtered_results)
        else:
            citations = {"citations": [], "citation_count": 0}

        return {
            "answer": response_text,
            "citations": citations.get("citations", []),
            "context": kb_context,
        }

    except Exception as e:
        logger.error(f"Generation failed: {e}")
        return {
            "answer": f"生成回答时出现错误: {str(e)}",
            "citations": [],
            "error": f"生成失败: {str(e)}",
        }


async def tool_call_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: 工具调用 - 用于未来ERP/CRM集成"""
    llm_client = state.get("_deps", {}).get("llm_client")
    tools_to_call = state.get("tools_to_call", [])
    tool_registry = state.get("_deps", {}).get("tool_registry", {})

    if not tools_to_call:
        return {"tool_results": []}

    results = []
    for tool_call in tools_to_call:
        func_name = tool_call.get("function", {}).get("name", "")
        arguments = tool_call.get("function", {}).get("arguments", "{}")

        tool_func = tool_registry.get(func_name)
        if tool_func:
            try:
                import json
                args = json.loads(arguments) if isinstance(arguments, str) else arguments
                result = await tool_func(**args)
                results.append({
                    "tool_call_id": tool_call.get("id", ""),
                    "function": func_name,
                    "result": result,
                })
            except Exception as e:
                results.append({
                    "tool_call_id": tool_call.get("id", ""),
                    "function": func_name,
                    "error": str(e),
                })
        else:
            results.append({
                "tool_call_id": tool_call.get("id", ""),
                "function": func_name,
                "error": f"Tool {func_name} not found",
            })

    return {"tool_results": results}


def route_after_optimize(state: Dict[str, Any]) -> str:
    """条件边: 根据意图分类路由到不同路径"""
    intent = state.get("intent", "kb_query")
    if intent == "general":
        return "general"
    return "kb_query"


async def reranker_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: Reranker精排 - 交叉编码器对粗检索结果重排序"""
    reranker_service = state.get("_deps", {}).get("reranker_service")
    filtered_results = state.get("filtered_results", [])
    optimized_query = state.get("optimized_query") or state.get("user_query", "")

    if not reranker_service or not reranker_service.is_available():
        logger.info("[reranker_node] Reranker不可用，跳过精排")
        return {}

    if not filtered_results:
        return {}

    try:
        # 提取每条结果的文本内容
        documents = [r.get("content", "") for r in filtered_results]
        scores = await reranker_service.rerank(optimized_query, documents)

        # 将 reranker_score 写入结果
        for i, r in enumerate(filtered_results):
            r["reranker_score"] = scores[i] if i < len(scores) else 0.0

        # 按 reranker_score 降序排列
        filtered_results.sort(key=lambda x: x.get("reranker_score", 0), reverse=True)

        top_k = 5
        filtered_results = filtered_results[:top_k]

        logger.info(f"[reranker_node] 精排完成，top分数: {[round(r.get('reranker_score', 0), 4) for r in filtered_results[:3]]}")
        return {"filtered_results": filtered_results}

    except Exception as e:
        logger.error(f"Reranker精排失败: {e}")
        return {}


async def check_retrieval_confidence_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: 检查检索置信度，低置信度时标记为兜底"""
    filtered_results = state.get("filtered_results", [])

    # 迭代补检索轮次：由充分性评估决定是否继续，不再降级通用模式
    if state.get("iteration_count", 0) > 0:
        logger.info(f"[check_retrieval_confidence] 迭代第{state['iteration_count']}轮，跳过低置信度降级")
        return {"retrieval_confident": True}

    if not filtered_results:
        logger.info("[check_retrieval_confidence] 无检索结果，标记为低置信度")
        return {"retrieval_confident": False}

    # 优先用 reranker 分数（交叉编码器，语义判断更准）
    # 回退到向量分数（reranker 不可用时）
    top_reranker_score = filtered_results[0].get("reranker_score", 0)
    top_vector_score = filtered_results[0].get("vector_score", 0)

    if top_reranker_score > 0:
        # reranker 可用：用 reranker 分数判断
        # cross-encoder 分数范围 0-1，表格行/短文本天然得分偏低，阈值不宜过高
        confidence_threshold = 0.10
        top_score = top_reranker_score
        score_name = "reranker_score"
    else:
        # reranker 不可用：回退到向量分数
        confidence_threshold = 0.40
        top_score = top_vector_score
        score_name = "vector_score"

    if top_score < confidence_threshold:
        # 多轮对话场景：query 中包含指代词时，即使检索分数低也走知识库
        # 因为 LLM 可以结合对话历史中的上下文来理解和回答
        user_query = state.get("user_query", "")
        has_reference = any(kw in user_query for kw in [
            "前面提到", "上面提到", "上面说的", "前面说的", "刚才提到", "刚才说的",
            "之前提到", "之前说的", "那个", "这位", "这个人", "他/she/她",
        ])
        if has_reference:
            logger.info(f"[check_retrieval_confidence] {score_name}={top_score:.4f} < {confidence_threshold}，但检测到指代词，走知识库模式")
            return {"retrieval_confident": True}

        # 实体精确命中兜底：查询中的实体词（如人名）在检索结果中逐字出现，
        # 说明知识库确有相关内容，不应因 reranker 对表格类内容打分偏低而误杀
        entities = _extract_entity_candidates(user_query)
        if entities:
            top_contents = [r.get("content", "") for r in filtered_results[:5]]
            hit = [ent for ent in entities if any(ent in c for c in top_contents)]
            if hit:
                logger.info(f"[check_retrieval_confidence] {score_name}={top_score:.4f} < {confidence_threshold}，但实体 {hit} 在检索结果中精确命中，走知识库模式")
                return {"retrieval_confident": True}

        logger.info(f"[check_retrieval_confidence] {score_name}={top_score:.4f} < {confidence_threshold}，标记为低置信度")
        return {"retrieval_confident": False}

    logger.info(f"[check_retrieval_confidence] {score_name}={top_score:.4f}，置信度足够")
    return {"retrieval_confident": True}


EVIDENCE_JUDGE_PROMPT = """你是检索充分性评估器。判断参考资料是否足以回答用户问题。

## 问题
{question}

## 参考资料
{context}

## 判断标准
1. 参考资料中存在能直接回答问题的具体信息 → sufficient=true
2. 参考资料相关但缺少回答问题所需的关键信息 → sufficient=false，并给出 sub_query（针对缺口的精确子查询）
3. 参考资料完全无关 → sufficient=false，sub_query 为改写后的更精准检索词
注意：如果问题是通用闲聊或明确无需知识库，sufficient=true。

只返回 JSON：{{"sufficient": true/false, "missing": "缺口描述", "sub_query": "补充检索词"}}"""

MAX_RETRIEVAL_ITERATIONS = 2


async def evidence_judge_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: 充分性评估，证据不足时触发补检索（最多 MAX_RETRIEVAL_ITERATIONS 轮）"""
    deps = state.get("_deps", {})
    judge_llm = deps.get("fast_llm_client") or deps.get("llm_client")
    intent = state.get("intent", "kb_query")
    filtered = state.get("filtered_results", [])
    iteration = state.get("iteration_count", 0)

    # 无需评估的情形
    if intent == "general" or not state.get("retrieval_confident", True) or not judge_llm:
        return {"need_more_evidence": False}
    if iteration >= MAX_RETRIEVAL_ITERATIONS:
        logger.info(f"[evidence_judge] 已达最大迭代轮次({iteration})，进入生成")
        return {"need_more_evidence": False}
    if not filtered:
        return {"need_more_evidence": False}

    question = state.get("optimized_query") or state.get("user_query", "")
    context = "\n\n".join(f"[{i+1}] {r.get('content', '')[:600]}" for i, r in enumerate(filtered[:5]))
    prompt = EVIDENCE_JUDGE_PROMPT.format(question=question, context=context)

    try:
        result = await judge_llm.generate(prompt, temperature=0.0)
        text = result.get("text", "") if isinstance(result, dict) else str(result)
        m = re.search(r'\{[\s\S]*\}', text)
        data = json.loads(m.group()) if m else {}
    except Exception as e:
        logger.warning(f"[evidence_judge] 评估失败，默认充分: {e}")
        return {"need_more_evidence": False}

    if data.get("sufficient", True):
        logger.info("[evidence_judge] 证据充分，进入生成")
        return {"need_more_evidence": False}

    sub_q = (data.get("sub_query") or "").strip()
    if not sub_q:
        return {"need_more_evidence": False}

    logger.info(f"[evidence_judge] 证据不足({data.get('missing', '')})，第{iteration + 1}轮补检索: {sub_q}")
    return {
        "need_more_evidence": True,
        "iteration_count": iteration + 1,
        "optimized_query": sub_q,
        "query_vector": None,
        "accumulated_results": (state.get("accumulated_results") or []) + filtered,
    }


def route_after_evidence_judge(state: Dict[str, Any]) -> str:
    """条件边: 充分性评估后的路由"""
    return "retrieve_more" if state.get("need_more_evidence") else "generate"


def should_use_tools(state: Dict[str, Any]) -> str:
    """条件边: 判断是否需要调用工具"""
    tools_to_call = state.get("tools_to_call", [])
    if tools_to_call:
        return "tool_call"
    return "generate"
