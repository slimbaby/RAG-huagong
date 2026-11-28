"""
LangGraph节点函数 - RAG流程的各个步骤
"""

from typing import Dict, Any, List, Optional
import asyncio
import contextlib
import inspect
import json
import logging
import re
import uuid

from ..core.config import settings
from ..prompts.rag import (
    BUSINESS_PROMPT,
    COMPARISON_PROMPT,
    GENERAL_PROMPT,
    RAG_PROMPT_WITH_CITATION_AND_TIME,
)
from ..tracing.tracer import SpanType

logger = logging.getLogger(__name__)


def _tracer_span(tracer, span_type: SpanType, name: str, inputs: Dict = None):
    """无 tracer 时返回空上下文，避免每个埋点都写一遍 if"""
    if not tracer:
        return contextlib.nullcontext()
    return tracer.start_span(span_type, name, inputs=inputs or {})


try:
    from ..tools.agent_tools import (
        TOOLS_SCHEMA,
        get_tools_schema,
        get_tool_registry,
        ENTRY_AGENT_SYSTEM_PROMPT,
    )
except ImportError:
    TOOLS_SCHEMA = []
    get_tools_schema = lambda *, entry_action=False: []
    get_tool_registry = lambda *, entry_action=False: {}
    ENTRY_AGENT_SYSTEM_PROMPT = ""

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

    # 流程办理：意图由后端规则流水线判定完毕，不再叠一层 LLM 改写（零额外调用）
    if state.get("flow_id") and state.get("flow_action") == "action":
        return {
            "optimized_query": user_query,
            "query_type": "factual",
            "intent": "business_query",
            "keywords": [],
            "expanded_queries": [],
            "sub_queries": [],
            "entry_action": bool(state.get("entry_action")),
        }

    logger.info(f"[query_optimize_node] user_query='{user_query}', query_rewriter={'有' if query_rewriter else '无'}")

    if not query_rewriter:
        return {
            "optimized_query": user_query,
            "query_type": "factual",
            "keywords": [],
            "expanded_queries": [],
            "sub_queries": [],
        }

    try:
        # 拆出真实 LLM 调用耗时，避免 query_optimize 整体慢时分不清是改写还是网络
        with _tracer_span(tracer, SpanType.LLM_CALL, "query_rewrite", {"user_query": user_query}):
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
            # 意图只在 API 层判定一次（_derive_entry_action），graph 不重算；
            # 否则会把 API 层判 False 的句子翻成 True，导致 generate 走进园 Agent
            # 却没按 True 绑定请求级身份 / slots。
            "entry_action": bool(state.get("entry_action")),
            "keywords": optimized.keywords,
            "expanded_queries": optimized.expanded_queries or [],
            "sub_queries": optimized.sub_queries or [],
        }
    except Exception as e:
        logger.error(f"Query optimization failed: {e}")
        return {
            "optimized_query": user_query,
            "query_type": "factual",
            "intent": "kb_query",
            "keywords": [],
            "expanded_queries": [],
            "sub_queries": [],
            "error": f"查询优化失败: {str(e)}",
        }


async def vectorize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点2: 查询向量化"""
    embedding_service = state.get("_deps", {}).get("embedding_service")
    optimized_query = state.get("optimized_query") or state.get("user_query", "")

    tracer = state.get("_deps", {}).get("tracer")

    if not embedding_service:
        return {"query_vector": None}

    try:
        with _tracer_span(tracer, SpanType.VECTOR_SEARCH, "embed_query", {"text": optimized_query[:200]}):
            vector = await embedding_service.embed_query(optimized_query)
        return {"query_vector": vector}
    except Exception as e:
        logger.error(f"Vectorization failed: {e}")
        return {"query_vector": None, "error": f"向量化失败: {str(e)}"}


async def retrieval_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点3: 混合检索 - 向量检索 + BM25检索"""
    hybrid_store = state.get("_deps", {}).get("hybrid_store")
    embedding_service = state.get("_deps", {}).get("embedding_service")
    tracer = state.get("_deps", {}).get("tracer")
    # 直接从state获取，确保不会丢失用户原始问题
    user_query = state.get("user_query", "")
    optimized_query = state.get("optimized_query") or user_query
    query_vector = state.get("query_vector")
    expanded_queries = state.get("expanded_queries", [])
    sub_queries = state.get("sub_queries", [])
    # RBAC: 用户可见 doc_id 集合（None = admin 不过滤）
    allowed_doc_ids = state.get("allowed_doc_ids")

    logger.info(f"[retrieval_node] optimized_query='{optimized_query}', query_vector={'有' if query_vector else '无'}, expanded_queries={len(expanded_queries)}, sub_queries={sub_queries}, allowed_doc_ids={'all' if allowed_doc_ids is None else len(allowed_doc_ids)}")

    if not hybrid_store:
        logger.warning("[retrieval_node] hybrid_store为空，跳过检索")
        return {"raw_results": [], "filtered_results": []}

    # 空集合表示用户没有任何可见文档，直接返回
    if allowed_doc_ids is not None and len(allowed_doc_ids) == 0:
        logger.info("[retrieval_node] 用户无任何可见文档，跳过检索")
        return {"raw_results": [], "filtered_results": []}

    try:
        if sub_queries and embedding_service:
            # 比较类查询分解：主查询 + 子查询并行检索
            logger.info(f"[retrieval_node] comparison decomposition: {len(sub_queries)} sub-queries: {sub_queries}")

            async def _search_with_embed(query_text: str, top_k: int, existing_vector=None):
                """Embed + search for a single query."""
                qv = existing_vector if existing_vector is not None else await embedding_service.embed_query(query_text)
                return await hybrid_store.hybrid_search(
                    query=query_text, query_vector=qv, top_k=top_k,
                    allowed_doc_ids=allowed_doc_ids,
                )

            # 并行任务：主查询（用已有向量）+ 各子查询（需独立 embedding）
            tasks = [_search_with_embed(optimized_query, settings.COMPARISON_MAIN_TOP_K, existing_vector=query_vector)]
            for sq in sub_queries:
                tasks.append(_search_with_embed(sq, settings.COMPARISON_SUB_TOP_K))

            with _tracer_span(tracer, SpanType.RETRIEVAL, "parallel_comparison_search", {"sub_queries": sub_queries}):
                search_results = await asyncio.gather(*tasks)

            # 合并所有结果
            results = []
            for sr in search_results:
                results.extend(sr)
            logger.info(f"[retrieval_node] parallel comparison search: {len(results)} total results from {len(tasks)} searches")
        else:
            # 原有路径：单查询检索
            with _tracer_span(tracer, SpanType.RETRIEVAL, "hybrid_search", {"query": optimized_query, "top_k": settings.RETRIEVAL_TOP_K}) as sp:
                results = await hybrid_store.hybrid_search(
                    query=optimized_query,
                    query_vector=query_vector,
                    top_k=settings.RETRIEVAL_TOP_K,
                    allowed_doc_ids=allowed_doc_ids,
                )
                if sp is not None:
                    sp.set_attribute("result_count", len(results))
            logger.info(f"[retrieval_node] hybrid_search返回 {len(results)} 条结果")

            # If not enough results, try expanded queries
            if len(results) < settings.EXPAND_TRIGGER_MIN_RESULTS and expanded_queries and embedding_service:
                for eq in expanded_queries[:settings.EXPAND_MAX_VARIANTS]:
                    with _tracer_span(tracer, SpanType.RETRIEVAL, "expanded_search", {"query": eq}):
                        eq_vector = await embedding_service.embed_query(eq)
                        eq_results = await hybrid_store.hybrid_search(
                            query=eq, query_vector=eq_vector, top_k=settings.QUERY_EXPAND_TOP_K,
                            allowed_doc_ids=allowed_doc_ids,
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

        # 把本轮（已合并前序轮次的）结果写回 state：补检索回边进来时，
        # 下一轮 retrieval 才能在上轮基础上合并，而不是直接覆盖丢掉
        return {
            "raw_results": result_dicts,
            "filtered_results": result_dicts,
            "accumulated_results": result_dicts,
        }

    except Exception as e:
        logger.error(f"Retrieval failed: {e}")
        return {"raw_results": [], "filtered_results": [], "error": f"检索失败: {str(e)}"}


# 三套生成模板（RAG_PROMPT_WITH_CITATION_AND_TIME / GENERAL_PROMPT /
# COMPARISON_PROMPT）已外部化到 app/prompts/rag.py，顶部统一 import


async def _probe_denied_scopes(state: Dict[str, Any], query: str) -> List[str]:
    """在用户 scope 之外做一次轻量探测（top_k=3，不过滤），
    返回「本应命中但被权限拦截」的 scope 显示名列表。空列表表示没有越权内容。"""
    deps = state.get("_deps", {})
    hybrid_store = deps.get("hybrid_store")
    embedding_service = deps.get("embedding_service")
    user_visible_scopes = state.get("user_visible_scopes") or []
    if not hybrid_store or not embedding_service:
        return []
    try:
        qv = await embedding_service.embed_query(query)
        # 不带 scope 过滤做一次轻量检索
        probe = await hybrid_store.hybrid_search(query=query, query_vector=qv, top_k=settings.SCOPE_PROBE_TOP_K, allowed_doc_ids=None)
        if not probe:
            return []
        # 收集命中结果的 doc_id → 查 scope
        from ..services import permission_service
        seen_scopes = set()
        hints = []
        for r in probe[:3]:
            doc_id = (r.get("metadata") or {}).get("doc_id") if isinstance(r, dict) else None
            if not doc_id:
                continue
            doc_scopes = await permission_service.get_doc_scopes(doc_id)
            for sid in doc_scopes:
                if sid in seen_scopes or sid in user_visible_scopes:
                    continue
                seen_scopes.add(sid)
                # 把 scope_id 翻译成显示名
                from ..services.permission_service import BUILTIN_SCOPES
                name_map = {s["scope_id"]: s["name"] for s in BUILTIN_SCOPES}
                hints.append(name_map.get(sid, sid))
        return hints
    except Exception as e:
        logger.warning(f"scope 兜底探测失败: {e}")
        return []


async def _chat_with_tools(llm_client, messages, tools_schema, tool_registry, max_rounds=2, token_queue=None):
    """带工具调用循环的对话。返回 {"text", "usage", "tool_trace", "entry_payload"}

    方案乙新增：工具输出里带 __entry_payload__ 标记时把它原样带出，
    这样结构化结果不必依赖 contextvar 也能回到 state / SSE done。
    """
    tool_trace = []
    entry_payload = {}
    msgs = list(messages)
    usage_total = {}
    for _ in range(max_rounds + 1):
        result = await llm_client.chat_with_tools(msgs, tools=tools_schema)
        for k in ("prompt_tokens", "completion_tokens"):
            usage_total[k] = usage_total.get(k, 0) + result.get("usage", {}).get(k, 0)
        calls = result.get("tool_calls") or []
        if not calls:
            if token_queue:
                token_queue.put_nowait(None)  # 非流式路径，直接放哨兵
            return {"text": result.get("text", ""), "usage": usage_total,
                    "tool_trace": tool_trace, "entry_payload": entry_payload}
        # 严格 OpenAI 兼容：每个 tool_call 必须带 type，DeepSeek V4 会做硬校验
        for c in calls:
            c.setdefault("type", "function")
        assistant_msg = {"role": "assistant", "content": result.get("text") or "", "tool_calls": calls}
        # 思考模式模型要求把 reasoning_content 随 assistant 消息原样回传；
        # 非思考模型没有该字段，保持不附加，避免向其它供应商发送未知字段。
        if result.get("reasoning_content"):
            assistant_msg["reasoning_content"] = result["reasoning_content"]
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
                    # 工具可能是 async（如 workflow 下单），也可能是普通同步函数。
                    # 同步工具多为文件/数据库 I/O，放到线程执行，避免阻塞事件循环。
                    if inspect.iscoroutinefunction(fn):
                        output = await fn(**args)
                    else:
                        output = await asyncio.to_thread(fn, **args)
                except Exception as e:
                    output = json.dumps({"error": str(e)}, ensure_ascii=False)
            logger.info(f"[tool_loop] {fn_name}({args}) -> {str(output)[:120]}")
            tool_trace.append({"name": fn_name, "args": args, "output_preview": str(output)[:200]})
            try:
                parsed_output = json.loads(output) if isinstance(output, str) else output
                if isinstance(parsed_output, dict) and (
                    parsed_output.get("__flow_payload__") or parsed_output.get("__entry_payload__")
                ):
                    entry_payload = parsed_output
            except (ValueError, TypeError):
                pass
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": output})
    # 轮次用尽，最后一次强制无工具回答
    if token_queue:
        token_queue.put_nowait(None)  # 非流式路径，直接放哨兵
    result = await llm_client.chat(msgs)
    return {"text": result["text"], "usage": usage_total,
            "tool_trace": tool_trace, "entry_payload": entry_payload}


async def _stream_to_queue(stream, queue: asyncio.Queue) -> Dict:
    """消费 LLM 流式输出，每个 token 写入 queue，返回完整结果 dict。
    stream 应为 LLMService.generate_stream / chat_stream 的 async generator，
    yield 的是 str (token 文本)。"""
    full_text = []
    count = 0
    async for token in stream:
        full_text.append(token)
        queue.put_nowait(token)
        count += 1
        if count <= 3:
            logger.info(f"[_stream_to_queue] token #{count}: {token!r}")
    queue.put_nowait(None)  # 哨兵：流式结束
    logger.info(f"[_stream_to_queue] 完成, 共 {count} tokens, 总长 {len(''.join(full_text))}")
    return {"text": "".join(full_text), "usage": {}}


def _entry_answer_from_payload(payload: Dict[str, Any]) -> str:
    """LLM 不可用 / 没调工具时，按确定性结果生成回复，保证链路不哑火。"""
    if payload.get("complete"):
        return (
            "信息已经齐了，下面这张核对卡请你逐项看一下。确认无误后，"
            "点「去表单提交」由你本人提交；AI 不会替你提交，也不会写入数据库。"
        )
    missing = (payload.get("missing") or [])[:4]
    errors = payload.get("errors") or []
    parts = []
    if missing:
        parts.append("还缺：" + "、".join(missing))
    if errors:
        parts.append("需要修正：" + "；".join(errors))
    if not parts:
        parts.append("请把入园申请的信息发我，照片和证件可以直接上传")
    return "。".join(parts) + "。可以一次性把这几项发给我，我会帮你填进申请表，最后由你确认提交。"


async def _generate_flow(state: Dict[str, Any], deps: Dict[str, Any], llm_client,
                         memory_manager, tracer, session_id: str,
                         user_query: str) -> Dict[str, Any]:
    """通用流程办理 Agent：按 flow_id 注入四件套，产出结构化字段，绝不写库。

    与 _generate_entry 的区别：字段、工具 schema、话术、抽取规则全部来自
    已发布配置；工具名全平台只有一份，参数按 flow_id 派生（§5.1）。
    LLM 不可用时走配置驱动的确定性抽取兜底，保证链路离线可跑。
    """
    from ..flows import context as flow_ctx
    from ..flows.tools import (
        FLOW_TOOL_REGISTRY, flow_system_prompt, flow_tools_schema,
        tool_get_my_attachments,
    )

    definition = flow_ctx.definition()
    if definition is None:
        return {"answer": "当前流程配置不可用，请稍后重试或联系管理员。",
                "citations": [], "context": "", "flow_payload": {}, "flow_slots": {}}

    known = definition.sanitize_slots(state.get("flow_slots") or flow_ctx.slots() or {})
    flow_ctx.set_payload({})
    payload: Dict[str, Any] = {}
    answer = ""

    if llm_client:
        history = []
        if memory_manager and session_id:
            try:
                history = await memory_manager.get_chat_history(session_id, limit=6)
            except Exception as e:
                logger.warning(f"[flow_agent] 会话历史读取失败: {e}")
        system = (flow_system_prompt(definition)
                  + "\n\n## 已收集字段（不要重复索要，只问缺失项）\n"
                  + json.dumps(known, ensure_ascii=False))
        messages = [{"role": "system", "content": system}]
        for m in history[-6:]:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": str(m["content"])[:800]})
        messages.append({"role": "user", "content": user_query})
        try:
            res = await _chat_with_tools(
                llm_client, messages,
                flow_tools_schema(definition), FLOW_TOOL_REGISTRY,
                max_rounds=4, token_queue=state.get("_token_queue"),
            )
            answer = res.get("text") or ""
            payload = res.get("entry_payload") or flow_ctx.get_payload() or {}
            usage = res.get("usage", {})
            if tracer:
                tracer.record_tokens(usage.get("prompt_tokens", 0),
                                     usage.get("completion_tokens", 0))
        except Exception as e:
            logger.warning(f"[flow_agent] LLM 工具循环失败，转规则兜底: {e}")
            answer = ""

    if not payload:
        merged = definition.merge_slots(known, definition.extract_by_rules(user_query))
        flow_ctx.set_payload(definition.build_payload(merged))
        try:
            await tool_get_my_attachments()
        except Exception as e:
            logger.warning(f"[flow_agent] 附件补齐失败: {e}")
        payload = flow_ctx.get_payload() or definition.build_payload(merged)

    payload = dict(payload)
    payload["slots"] = definition.merge_slots(known, payload.get("slots") or {})
    check = definition.validate(payload["slots"], for_submit=False)
    payload["missing"] = check["missing"]
    payload["errors"] = check["errors"]
    payload["complete"] = check["complete"]

    # 卡片只在信息核对完成时下发；「提交后不再重出卡」由意图路由层的
    # session_completed 收敛负责（见 api/chat.py::_resolve_flow），不在生成层
    # 按槽位是否变化推断——那会误伤「刚上传完附件、字段齐全」的正常出卡轮次。
    if not payload["complete"]:
        payload["card"] = None
    elif not payload.get("card"):
        payload["card"] = definition.build_card(payload["slots"])

    if not answer.strip():
        answer = _entry_answer_from_payload(payload)
    for forbidden in ("已提交成功", "已经提交申请", "申请已受理"):
        if forbidden in answer:
            answer = answer.replace(forbidden, "信息已整理好（尚未提交）")

    # 兼容层：旧前端仍读 entry_payload / entry_slots
    return {
        "answer": answer, "citations": [], "context": "",
        "flow_payload": payload, "flow_slots": payload["slots"],
        "entry_payload": payload, "entry_slots": payload["slots"],
        "tool_results": [],
    }


async def _generate_entry(state: Dict[str, Any], deps: Dict[str, Any], llm_client,
                          memory_manager, tracer, session_id: str,
                          user_query: str) -> Dict[str, Any]:
    """入园办理单 Agent：只注入四件套工具，产出结构化字段，绝不写库。

    LLM 不可用（密钥失效/断网）时走规则抽取兜底，仍然能出 slots 与追问，
    只是不生成自然语言润色——保证业务链路可离线运行与联调。
    """
    from ..services import entry_schema as entry_schema
    from ..core import entry_context as entry_ctx
    from ..tools.entry_tools import tool_get_my_entry_attachments

    known = entry_schema.sanitize_slots(state.get("entry_slots") or {})
    entry_ctx.set_payload({})
    payload: Dict[str, Any] = {}
    answer = ""

    if llm_client:
        history = []
        if memory_manager and session_id:
            try:
                history = await memory_manager.get_chat_history(session_id, limit=6)
            except Exception as e:
                logger.warning(f"[entry_agent] 会话历史读取失败: {e}")
        system = (
            ENTRY_AGENT_SYSTEM_PROMPT
            + "\n\n## 已收集字段（不要重复索要，只问缺失项）\n"
            + entry_schema.to_json(known)
        )
        messages = [{"role": "system", "content": system}]
        for m in history[-6:]:
            if m.get("role") in ("user", "assistant") and m.get("content"):
                messages.append({"role": m["role"], "content": str(m["content"])[:800]})
        messages.append({"role": "user", "content": user_query})
        try:
            res = await _chat_with_tools(
                llm_client, messages,
                get_tools_schema(entry_action=True),
                get_tool_registry(entry_action=True),
                max_rounds=4,
                token_queue=state.get("_token_queue"),
            )
            answer = res.get("text") or ""
            payload = res.get("entry_payload") or entry_ctx.get_payload() or {}
            usage = res.get("usage", {})
            if tracer:
                tracer.record_tokens(usage.get("prompt_tokens", 0),
                                     usage.get("completion_tokens", 0))
        except Exception as e:
            logger.warning(f"[entry_agent] LLM 工具循环失败，转规则兜底: {e}")
            answer = ""

    if not payload:
        # 确定性兜底：已有字段 + 本轮原文正则抽取 + 显式附件 ID
        merged = entry_schema.merge_slots(known, entry_schema.extract_by_rules(user_query))
        entry_ctx.set_payload(entry_schema.build_entry_payload(merged))
        try:
            await tool_get_my_entry_attachments()
        except Exception as e:
            logger.warning(f"[entry_agent] 附件补齐失败: {e}")
        payload = entry_ctx.get_payload() or entry_schema.build_entry_payload(merged)

    # 模型可能只回传部分字段，统一以「已知字段 ∪ 工具结果」为准
    payload = dict(payload)
    payload["slots"] = entry_schema.merge_slots(known, payload.get("slots") or {})
    check = entry_schema.validate(payload["slots"], for_submit=False)
    payload["missing"] = check["missing"]
    payload["errors"] = check["errors"]
    payload["complete"] = check["complete"]
    if not payload["complete"]:
        payload["card"] = None
    elif not payload.get("card"):
        payload["card"] = entry_schema.build_card(payload["slots"])

    if not answer.strip():
        answer = _entry_answer_from_payload(payload)
    # 安全兜底：无论模型说什么，都不能让司机误以为已经提交
    for forbidden in ("已提交成功", "已经提交申请", "申请已受理"):
        if forbidden in answer:
            answer = answer.replace(forbidden, "信息已整理好（尚未提交）")

    return {
        "answer": answer,
        "citations": [],
        "context": "",
        "entry_payload": payload,
        "entry_slots": payload["slots"],
        "tool_results": [],
    }


async def generate_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点4: 生成回答（支持多轮记忆 + 工具调用循环 + 入园办理 Agent）"""
    deps = state.get("_deps", {})
    llm_client = deps.get("llm_client")
    citation_service = deps.get("citation_service")
    memory_manager = deps.get("memory_manager")
    optimized_query = state.get("optimized_query") or state.get("user_query", "")
    filtered_results = state.get("filtered_results", [])
    intent = state.get("intent", "kb_query")
    query_type = state.get("query_type", "factual")
    retrieval_confident = state.get("retrieval_confident", True)
    session_id = state.get("session_id", "")
    tracer = deps.get("tracer")
    tool_registry = deps.get("tool_registry")
    lesson_store = deps.get("lesson_store")
    # RBAC 兜底：检索为空时探测其他 scope 是否有结果
    user_visible_scopes = state.get("user_visible_scopes")
    denied_scope_hints = list(state.get("denied_scope_hints") or [])

    # ── 反思教训注入：系统级无条件 + 会话级按 session 召回 ──
    # 教训是"行为准则"，与长期记忆/参考资料分属不同语义位，单独成段
    lesson_text = "（无）"
    # 评测请求（session_id 由 EvalRunner 以 eval_ 开头生成）不注入反思教训，
    # 否则上一批评测写入的系统级教训会污染后续批次，A/B 分数不可比。
    if lesson_store and not session_id.startswith("eval_"):
        try:
            lesson_text = lesson_store.build_lesson_text(session_id=session_id)
        except Exception as e:
            logger.warning(f"[generate_node] 教训召回失败，跳过注入: {e}")
            lesson_text = "（无）"

    # 通用流程办理：后端权威路由命中后直接切单 Agent 工具循环，
    # 按 flow_id 注入配置派生的四件套，不走检索、不写库。
    if state.get("flow_id") and state.get("flow_action") == "action":
        return await _generate_flow(
            state, deps, llm_client, memory_manager, tracer, session_id,
            state.get("user_query") or optimized_query,
        )

    # 入园办理兼容层：仅当旧链路单独命中时走原四件套。
    if state.get("entry_action"):
        return await _generate_entry(
            state, deps, llm_client, memory_manager, tracer, session_id,
            state.get("user_query") or optimized_query,
        )

    if not llm_client:
        return {
            "answer": "LLM服务未配置，无法生成回答。",
            "citations": [],
        }

    try:
        # 空证据 + 低置信度必须走通用兜底。此前条件含 `not tool_registry`，
        # 而工具注册表恒非空，导致补检索后仍为空时又套上知识库强约束 prompt。
        use_general = (intent in ("general", "business_query")) or (
            not retrieval_confident and not filtered_results
        )

        if use_general:
            kb_context = ""
            logger.info(f"[generate_node] 使用通用问答模式, intent={intent}, retrieval_confident={retrieval_confident}")
        else:
            if citation_service:
                kb_context = citation_service.format_context_with_citation_and_time(filtered_results)
            else:
                kb_context = "\n\n".join([r.get("content", "") for r in filtered_results])
            logger.info(f"[generate_node] optimized_query='{optimized_query}', filtered_results={len(filtered_results)}条, context长度={len(kb_context)}")

        # ── scope 越权兜底：检索为空 + 用户有 scope 限制 → 探测其他 scope 是否有结果 ──
        if (
            not filtered_results
            and intent == "kb_query"
            and user_visible_scopes is not None
            and "all" not in (user_visible_scopes or [])
            and not denied_scope_hints
        ):
            denied_scope_hints = await _probe_denied_scopes(state, optimized_query)

        # 流式输出：有 token queue 时启用
        token_queue = state.get("_token_queue")
        use_streaming = token_queue is not None

        if memory_manager and session_id:
            messages, relevant_memories = await memory_manager.build_context(
                session_id=session_id,
                user_query=optimized_query,
                kb_context=kb_context,
                intent=intent,
                retrieval_confident=retrieval_confident,
                query_type=query_type,
                lesson_text=lesson_text,
            )
            logger.info(f"[generate_node] 使用记忆模式, messages={len(messages)}条, 长期记忆={len(relevant_memories)}条")
            if tool_registry:
                result = await _chat_with_tools(
                    llm_client, messages,
                    get_tools_schema(entry_action=False),
                    get_tool_registry(entry_action=False),
                    token_queue=token_queue,
                )
            elif use_streaming:
                result = await _stream_to_queue(llm_client.chat_stream(messages), token_queue)
            else:
                result = await llm_client.chat(messages)
        else:
            if intent == "business_query":
                prompt = BUSINESS_PROMPT.format(question=optimized_query)
            elif use_general:
                prompt = GENERAL_PROMPT.format(question=optimized_query)
            elif query_type == "comparison":
                prompt = COMPARISON_PROMPT.format(context=kb_context, question=optimized_query)
            else:
                prompt = RAG_PROMPT_WITH_CITATION_AND_TIME.format(
                    context=kb_context, question=optimized_query
                )
            logger.info(f"[generate_node] 使用单轮模式（无记忆系统）, query_type={query_type}")
            # 单轮模式没有 build_context 承载教训段，这里直接追加到 prompt 末尾，
            # 保证有无记忆系统两条路径的教训注入语义一致
            if lesson_text and lesson_text != "（无）":
                prompt += f"\n\n## 历史教训（同类问题的注意事项）\n{lesson_text}"
            if tool_registry:
                messages = [{"role": "user", "content": prompt}]
                result = await _chat_with_tools(
                    llm_client, messages,
                    get_tools_schema(entry_action=False),
                    get_tool_registry(entry_action=False),
                    token_queue=token_queue,
                )
            elif use_streaming:
                result = await _stream_to_queue(llm_client.generate_stream(prompt), token_queue)
            else:
                result = await llm_client.generate(prompt)

        response_text = result["text"]

        # ── 注入 scope 越权兜底提示 ──
        if denied_scope_hints and not filtered_results:
            scope_names = "、".join(denied_scope_hints)
            hint = f"\n\n---\n💡 提示：您询问的内容可能属于「{scope_names}」部门的资料，当前账号暂无该部门查看权限，请联系相关人员获取。"
            response_text = response_text + hint
            logger.info(f"[generate_node] 注入 scope 越权提示: {scope_names}")

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
            "denied_scope_hints": denied_scope_hints,
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
    """条件边: 根据意图分类路由到不同路径。

    入园办理（含同一会话里的补信息轮次）一律直达 generate，
    跳过 vectorize/retrieval/reranker，避免白跑向量检索。
    """
    if state.get("flow_id") and state.get("flow_action") == "action":
        return "business"
    if state.get("entry_action"):
        return "business"
    intent = state.get("intent", "kb_query")
    if intent == "general":
        return "general"
    if intent == "business_query":
        return "business"
    return "kb_query"


async def reranker_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: Reranker精排 - 交叉编码器对粗检索结果重排序"""
    reranker_service = state.get("_deps", {}).get("reranker_service")
    tracer = state.get("_deps", {}).get("tracer")
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
        # 精排是已知最大瓶颈，单独拆出来观察
        with _tracer_span(tracer, SpanType.RETRIEVAL, "rerank_inference", {"doc_count": len(documents)}) as sp:
            scores = await reranker_service.rerank(optimized_query, documents)
            if sp is not None:
                sp.set_attribute("doc_count", len(documents))

        # 将 reranker_score 写入结果
        for i, r in enumerate(filtered_results):
            r["reranker_score"] = scores[i] if i < len(scores) else 0.0

        # 按 reranker_score 降序排列
        filtered_results.sort(key=lambda x: x.get("reranker_score", 0), reverse=True)

        top_k = settings.RERANKER_TOP_K
        filtered_results = filtered_results[:top_k]

        logger.info(f"[reranker_node] 精排完成，top分数: {[round(r.get('reranker_score', 0), 4) for r in filtered_results[:3]]}")
        return {"filtered_results": filtered_results}

    except Exception as e:
        logger.error(f"Reranker精排失败: {e}")
        return {}


async def check_retrieval_confidence_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: 检查检索置信度，低置信度时标记为兜底"""
    filtered_results = state.get("filtered_results", [])

    # 空结果必须先判：补检索轮次也不能把「仍然没有证据」误标为高置信度，
    # 否则 generate 会带着空 context 走知识库强约束 prompt，抬高编造概率。
    if not filtered_results:
        logger.info("[check_retrieval_confidence] 无检索结果，标记为低置信度")
        return {"retrieval_confident": False}

    # 迭代补检索轮次：由充分性评估决定是否继续，不再降级通用模式
    if state.get("iteration_count", 0) > 0:
        logger.info(f"[check_retrieval_confidence] 迭代第{state['iteration_count']}轮，跳过低置信度降级")
        return {"retrieval_confident": True}

    # 优先用 reranker 分数（交叉编码器，语义判断更准）
    # 回退到向量分数（reranker 不可用时）
    top_reranker_score = filtered_results[0].get("reranker_score", 0)
    top_vector_score = filtered_results[0].get("vector_score", 0)

    if top_reranker_score > 0:
        # reranker 可用：用 reranker 分数判断
        # cross-encoder 分数范围 0-1，表格行/短文本天然得分偏低，阈值不宜过高
        confidence_threshold = settings.CONFIDENCE_RERANKER_THRESHOLD
        top_score = top_reranker_score
        score_name = "reranker_score"
    else:
        # reranker 不可用：回退到向量分数
        confidence_threshold = settings.CONFIDENCE_VECTOR_THRESHOLD
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


async def evidence_judge_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """节点: 检索结果为空时触发一轮补检索，有结果时直接放行"""
    intent = state.get("intent", "kb_query")
    filtered = state.get("filtered_results", [])
    iteration = state.get("iteration_count", 0)

    # 通用模式 → 不需要补检索
    if intent == "general":
        return {"need_more_evidence": False}
    # 已有检索结果（含低分被救回/低分降级通用）→ 补检索无意义，放行
    if filtered:
        return {"need_more_evidence": False}
    # 已经补检索过一轮仍为空 → 放行，让 generate 兜底
    if iteration > 0:
        logger.info(f"[evidence_judge] 补检索第{iteration}轮仍为空，进入生成")
        return {"need_more_evidence": False}

    # 用户没有任何可见文档（空集 ≠ admin 的 None）→ 检索节点第一步就会短路返回空，
    # 补检索再跑一轮必然还是空。直接放行，让 generate 走 denied_scope_hints 的权限话术。
    allowed_doc_ids = state.get("allowed_doc_ids")
    if allowed_doc_ids is not None and len(allowed_doc_ids) == 0:
        logger.info("[evidence_judge] 用户无可见文档，跳过补检索")
        return {"need_more_evidence": False}

    # 注意：此处不能按 retrieval_confident 放行——check_confidence 对空结果
    # 必标 False，若据此放行则本补检索回边永不可达（修复前的死逻辑，
    # 2026-09-25 由 test_graph_runtime 冒烟测试发现）。补检索只针对「结果为空」。

    # 检索结果为空且是第一轮 → 必须换一个查询再试。
    # 第一轮用的就是 optimized_query，原样重跑等于同一 query + 同一向量，
    # 结果只会再次为空（expanded_queries 在 retrieval 首轮结果不足时也已试过）。
    optimized_query = state.get("optimized_query") or state.get("user_query", "")
    user_query = (state.get("user_query") or "").strip()
    keywords = [k for k in (state.get("keywords") or []) if k]

    if user_query and user_query != optimized_query.strip():
        # 改写版搜空，最常见原因是改写跑偏 → 退回用户原话再试
        retry_query, retry_note = user_query, "退回原始问句"
    elif keywords:
        # 原文与改写一致（改写没起作用）→ 用关键词拼一个更"硬"的查询
        retry_query, retry_note = " ".join(keywords), "改用关键词"
    else:
        retry_query, retry_note = optimized_query, "无可用变体，原查询重试"

    logger.info(f"[evidence_judge] 检索结果为空，第1轮补检索（{retry_note}）: {retry_query}")
    return {
        "need_more_evidence": True,
        "iteration_count": 1,
        "optimized_query": retry_query,
        "query_vector": None,
        "judge_note": retry_note,
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
