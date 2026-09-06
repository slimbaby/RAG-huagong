"""
聊天API - 基于LangGraph Agent
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime
import contextlib
import logging
import os
import json
import uuid as uuid_lib

from ..tracing.tracer import SpanType

router = APIRouter()
logger = logging.getLogger(__name__)

# 延迟初始化
_rag_graph = None
_agent_graph = None
_deps = None


def get_deps():
    global _deps
    if _deps is None:
        try:
            from ..core.config import settings

            from ..services.query_rewriter import QueryRewriter
            from ..services.citation_service import CitationService
            from ..services.llm_service import LLMService
            from ..core.model_config import ModelManager
            from ..services.embedding_service import EmbeddingService

            citation = CitationService()

            tracer = None
            try:
                from ..tracing.tracer import Tracer
                tracer = Tracer(storage_path="./logs/traces")
            except Exception:
                pass

            checkpointer = None
            try:
                from ..tracing.checkpointer import Checkpointer
                checkpointer = Checkpointer(storage_path="./checkpoints")
            except Exception:
                pass

            llm_client = None
            try:
                llm_client = LLMService(
                    api_key=getattr(settings, "PREMIUM_MODEL_API_KEY", ""),
                    base_url=getattr(settings, "PREMIUM_MODEL_API_BASE_URL", "https://api.xiaomi.com/v1"),
                    model_name=getattr(settings, "PREMIUM_MODEL_NAME", "mimo-72b"),
                )
            except Exception as e:
                logger.warning(f"LLM初始化失败: {e}")

            rewriter = QueryRewriter(llm_client=llm_client)

            embedding_service = None
            try:
                model_manager = ModelManager.from_env()
                embedding_config = model_manager.get_embedding()
                if embedding_config:
                    embedding_service = EmbeddingService(embedding_config)
            except Exception as e:
                logger.warning(f"Embedding初始化失败: {e}")

            reranker_service = None
            try:
                from ..services.reranker_service import RerankerService
                reranker_model = getattr(settings, "RERANKER_MODEL_NAME", "BAAI/bge-reranker-base")
                if getattr(settings, "RERANKER_ENABLED", True):
                    reranker_service = RerankerService(model_name=reranker_model)
                    if reranker_service.is_available():
                        logger.info(f"Reranker初始化完成: {reranker_model}")
                    else:
                        logger.warning("Reranker模型加载失败，将跳过精排")
            except Exception as e:
                logger.warning(f"Reranker初始化失败: {e}")

            store = None
            try:
                from ..vectorstore.shared_store import get_shared_store
                store = get_shared_store()
            except Exception as e:
                logger.warning(f"Milvus连接失败: {e}")

            # 记忆系统初始化
            session_store = None
            memory_manager = None
            if getattr(settings, "MEMORY_ENABLED", True):
                try:
                    from ..services.session_store import SessionStore
                    redis_url = settings.REDIS_URL
                    session_store = SessionStore(
                        redis_url=redis_url,
                        max_messages=getattr(settings, "MEMORY_SHORT_TERM_MAX_MESSAGES", 60),
                        ttl=getattr(settings, "MEMORY_SHORT_TERM_TTL", 86400),
                        password=getattr(settings, "REDIS_PASSWORD", ""),
                    )
                    logger.info("SessionStore初始化完成")
                except Exception as e:
                    logger.warning(f"SessionStore初始化失败: {e}")

                try:
                    from ..services.memory_extractor import MemoryExtractor
                    from ..services.memory_manager import MemoryManager
                    extractor = MemoryExtractor(llm_client=llm_client) if llm_client else None
                    if session_store:
                        memory_manager = MemoryManager(
                            session_store=session_store,
                            memory_extractor=extractor,
                            embedding_service=embedding_service,
                            llm_client=llm_client,
                            milvus_store=store,
                            config=settings,
                        )
                        logger.info("MemoryManager初始化完成")
                except Exception as e:
                    logger.warning(f"MemoryManager初始化失败: {e}")

            _deps = {
                "query_rewriter": rewriter,
                "citation_service": citation,
                "tracer": tracer,
                "checkpointer": checkpointer,
                "hybrid_store": store,
                "llm_client": llm_client,
                "embedding_service": embedding_service,
                "reranker_service": reranker_service,
                "session_store": session_store,
                "memory_manager": memory_manager,
                "tool_registry": None,
                "fast_llm_client": None,
            }

            try:
                from ..tools.agent_tools import TOOL_REGISTRY
                _deps["tool_registry"] = TOOL_REGISTRY
                logger.info("Agent工具集初始化完成")
            except Exception as e:
                logger.warning(f"Agent工具集加载失败: {e}")

            try:
                standard_key = getattr(settings, "STANDARD_MODEL_API_KEY", "")
                standard_url = getattr(settings, "STANDARD_MODEL_API_BASE_URL", "")
                standard_name = getattr(settings, "STANDARD_MODEL_NAME", "")
                if standard_key and standard_url and standard_name:
                    _deps["fast_llm_client"] = LLMService(
                        api_key=standard_key, base_url=standard_url, model_name=standard_name
                    )
                    logger.info("fast_llm_client初始化完成（用于证据评估/路由）")
            except Exception as e:
                logger.warning(f"fast_llm_client初始化失败: {e}")

            logger.info("依赖初始化完成")
        except Exception as e:
            logger.error(f"依赖初始化失败: {e}")
            _deps = None
    return _deps or {}


def get_rag_graph():
    global _rag_graph
    if _rag_graph is None:
        try:
            from ..agents.graph import create_rag_graph
            deps = get_deps()
            _rag_graph = create_rag_graph(deps=deps)
            logger.info(f"RAG Graph初始化完成, tool_registry={bool(deps.get('tool_registry'))}")
        except Exception as e:
            logger.error(f"RAG Graph初始化失败: {e}")
    return _rag_graph


def get_agent_graph():
    global _agent_graph
    if _agent_graph is None:
        try:
            from ..agents.graph import create_agent_graph
            deps = get_deps()
            _agent_graph = create_agent_graph(deps=deps)
            logger.info("Agent Graph初始化完成")
        except Exception as e:
            logger.error(f"Agent Graph初始化失败: {e}")
    return _agent_graph


class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题", min_length=1, max_length=2000)
    session_id: Optional[str] = Field(default=None, description="会话ID")
    stream: bool = Field(default=False, description="是否流式返回")
    use_agent: bool = Field(default=False, description="是否使用Agent模式（支持工具调用）")


class CitationInfo(BaseModel):
    citation_id: int
    source_file: str
    page_number: Optional[int] = None
    line_start: Optional[int] = None
    content_preview: str
    content: Optional[str] = None


class ChatResponse(BaseModel):
    task_id: str
    trace_id: Optional[str] = None
    answer: str
    citations: List[CitationInfo] = []
    optimization: Optional[Dict] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


@router.post("/query", response_model=ChatResponse, summary="知识库问答")
async def chat_query(request: ChatRequest):
    task_id = str(uuid_lib.uuid4())[:8]
    session_id = request.session_id or str(uuid_lib.uuid4())[:8]

    if request.use_agent:
        graph = get_agent_graph()
    else:
        graph = get_rag_graph()

    if graph is None:
        return ChatResponse(
            task_id="fallback",
            answer="RAG服务正在初始化，请稍后重试。如需完整功能，请确保Milvus、LLM和Embedding服务已正确配置。",
            optimization={"original": request.question, "rewritten": request.question, "type": "factual"}
        )

    deps = get_deps()
    tracer = deps.get("tracer")
    trace_id = None
    logger.info(f"[chat_query] deps: tool_registry={bool(deps.get('tool_registry'))}, fast_llm={bool(deps.get('fast_llm_client'))}")

    try:
        if tracer:
            trace_id = tracer.start_trace(
                user_query=request.question,
                task_id=task_id,
                session_id=session_id,
            )

        initial_state = {
            "user_query": request.question,
            "session_id": session_id,
            "task_id": task_id,
            "optimized_query": None,
            "query_type": None,
            "intent": None,
            "keywords": [],
            "expanded_queries": [],
            "query_vector": None,
            "raw_results": [],
            "filtered_results": [],
            "retrieval_confident": None,
            "context": None,
            "answer": None,
            "citations": [],
            "error": None,
            "metadata": {},
            "tools_to_call": [],
            "tool_results": [],
        }

        logger.info(f"[chat_query] initial_state user_query='{initial_state.get('user_query', '')}', state keys: {list(initial_state.keys())}")
        # 根 span：让后续所有节点 span 挂到它下面，形成真正的父子链路
        root_cm = (
            tracer.start_span(SpanType.REQUEST, "chat_query", inputs={"question": request.question, "session_id": session_id})
            if tracer else contextlib.nullcontext()
        )
        with root_cm:
            result = await graph.ainvoke(initial_state)
        logger.info(f"[chat_query] result keys: {list(result.keys()) if isinstance(result, dict) else 'not dict'}")

        answer_text = result.get("answer", "无法生成回答")
        citations_raw = result.get("citations", [])

        citations = []
        for i, c in enumerate(citations_raw[:10]):
            if isinstance(c, dict):
                citations.append(CitationInfo(
                    citation_id=i + 1,
                    source_file=c.get("source_file", "未知来源"),
                    page_number=c.get("page_number"),
                    line_start=c.get("line_start"),
                    content_preview=c.get("content_preview", "")[:500] if c.get("content_preview") else c.get("content", "")[:500],
                    content=c.get("content", "")
                ))

        if tracer and trace_id:
            tracer.end_trace(
                trace_id=trace_id,
                answer=answer_text,
                sources_count=len(citations_raw),
                status="ok",
            )

        # 保存对话到短期记忆，异步提取长期记忆
        memory_manager = deps.get("memory_manager")
        if memory_manager:
            try:
                await memory_manager.save_turn(session_id, request.question, answer_text, citations_raw)
                # 异步提取长期记忆（不阻塞响应）
                import asyncio
                asyncio.create_task(memory_manager.extract_and_store(session_id, request.question, answer_text))
            except Exception as e:
                logger.warning(f"记忆保存失败: {e}")

        return ChatResponse(
            task_id=task_id,
            trace_id=trace_id or result.get("trace_id"),
            answer=answer_text,
            citations=citations,
            optimization={
                "original": request.question,
                "rewritten": result.get("optimized_query", request.question),
                "type": result.get("query_type", "factual"),
                "keywords": result.get("keywords", []),
            }
        )

    except Exception as e:
        logger.error(f"聊天查询失败: {e}", exc_info=True)
        if tracer and trace_id:
            tracer.end_trace(
                trace_id=trace_id,
                status="error",
                error_message=str(e),
            )
        return ChatResponse(
            task_id="error",
            answer=f"处理问题时出现错误: {str(e)}",
            optimization={"original": request.question, "rewritten": request.question, "type": "error"}
        )


def _build_initial_state(question: str, session_id: str, task_id: str) -> Dict:
    return {
        "user_query": question,
        "session_id": session_id,
        "task_id": task_id,
        "optimized_query": None,
        "query_type": None,
        "intent": None,
        "keywords": [],
        "expanded_queries": [],
        "query_vector": None,
        "raw_results": [],
        "filtered_results": [],
        "retrieval_confident": None,
        "context": None,
        "answer": None,
        "citations": [],
        "error": None,
        "metadata": {},
        "tools_to_call": [],
        "tool_results": [],
    }


def _format_citations(citations_raw: List) -> List[CitationInfo]:
    citations = []
    for i, c in enumerate(citations_raw[:10]):
        if isinstance(c, dict):
            citations.append(CitationInfo(
                citation_id=i + 1,
                source_file=c.get("source_file", "未知来源"),
                page_number=c.get("page_number"),
                line_start=c.get("line_start"),
                content_preview=c.get("content_preview", "")[:500] if c.get("content_preview") else c.get("content", "")[:500],
                content=c.get("content", "")
            ))
    return citations


@router.post("/query/stream", summary="知识库问答（SSE流式）")
async def chat_query_stream(request: ChatRequest):
    task_id = str(uuid_lib.uuid4())[:8]
    session_id = request.session_id or str(uuid_lib.uuid4())[:8]

    if request.use_agent:
        graph = get_agent_graph()
    else:
        graph = get_rag_graph()

    if graph is None:
        async def fallback():
            yield f"data: {json.dumps({'type': 'error', 'message': 'RAG服务正在初始化'}, ensure_ascii=False)}\n\n"
        return StreamingResponse(fallback(), media_type="text/event-stream")

    deps = get_deps()
    tracer = deps.get("tracer")
    trace_id = None

    async def event_generator():
        nonlocal trace_id
        try:
            if tracer:
                trace_id = tracer.start_trace(
                    user_query=request.question,
                    task_id=task_id,
                    session_id=session_id,
                )

            initial_state = _build_initial_state(request.question, session_id, task_id)
            accumulated_state = dict(initial_state)

            from ..agents.graph import extract_span_outputs

            # 根 span：流式场景下 duration 含 SSE 推送耗时，已标注
            root_cm = (
                tracer.start_span(SpanType.REQUEST, "chat_query_stream", inputs={"question": request.question, "session_id": session_id})
                if tracer else contextlib.nullcontext()
            )
            with root_cm as root_span:
                if root_span is not None:
                    root_span.set_attribute("includes_sse_flush", True)
                async for chunk in graph.astream(initial_state):
                    for node_name, updates in chunk.items():
                        accumulated_state.update(updates)

                        step_data = extract_span_outputs(node_name, updates, accumulated_state)
                        event = {
                            "type": "step",
                            "node": node_name,
                            "status": "completed",
                            "data": step_data,
                        }
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            answer_text = accumulated_state.get("answer", "无法生成回答")
            citations_raw = accumulated_state.get("citations", [])
            citations = _format_citations(citations_raw)

            if tracer and trace_id:
                tracer.end_trace(
                    trace_id=trace_id,
                    answer=answer_text,
                    sources_count=len(citations_raw),
                    status="ok",
                )

            # 保存对话到短期记忆，异步提取长期记忆
            memory_manager = deps.get("memory_manager")
            if memory_manager:
                try:
                    await memory_manager.save_turn(session_id, request.question, answer_text, citations_raw)
                    import asyncio
                    asyncio.create_task(memory_manager.extract_and_store(session_id, request.question, answer_text))
                except Exception as e:
                    logger.warning(f"记忆保存失败: {e}")

            done_event = {
                "type": "done",
                "task_id": task_id,
                "trace_id": trace_id,
                "answer": answer_text,
                "citations": [c.model_dump() for c in citations],
                "optimization": {
                    "original": request.question,
                    "rewritten": accumulated_state.get("optimized_query", request.question),
                    "type": accumulated_state.get("query_type", "factual"),
                    "keywords": accumulated_state.get("keywords", []),
                },
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"SSE聊天查询失败: {e}", exc_info=True)
            if tracer and trace_id:
                tracer.end_trace(trace_id=trace_id, status="error", error_message=str(e))
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/history/{session_id}", summary="获取聊天历史")
async def get_chat_history(session_id: str, limit: int = 60):
    deps = get_deps()
    session_store = deps.get("session_store")
    if not session_store:
        return {"session_id": session_id, "history": []}
    try:
        messages = await session_store.get_messages(session_id, limit=limit)
        return {"session_id": session_id, "history": messages}
    except Exception as e:
        logger.error(f"获取聊天历史失败: {e}")
        return {"session_id": session_id, "history": []}


@router.delete("/history/{session_id}", summary="清空聊天历史")
async def clear_chat_history(session_id: str):
    deps = get_deps()
    session_store = deps.get("session_store")
    if session_store:
        try:
            await session_store.clear_session(session_id)
        except Exception as e:
            logger.error(f"清空聊天历史失败: {e}")
    return {"message": "历史已清空", "session_id": session_id}


@router.get("/sessions", summary="获取会话列表")
async def list_sessions(limit: int = 50):
    deps = get_deps()
    session_store = deps.get("session_store")
    if not session_store:
        return {"sessions": []}
    try:
        sessions = await session_store.list_sessions(limit=limit)
        return {"sessions": sessions}
    except Exception as e:
        logger.error(f"获取会话列表失败: {e}")
        return {"sessions": []}


@router.get("/memories/stats", summary="获取记忆统计")
async def get_memory_stats():
    deps = get_deps()
    memory_manager = deps.get("memory_manager")
    if not memory_manager:
        return {"total": 0, "active": 0, "expired": 0, "by_type": {}}
    return memory_manager.get_memory_stats()
