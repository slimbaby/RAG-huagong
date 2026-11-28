"""
聊天API - 基于LangGraph Agent
"""

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import asyncio
import contextlib
import logging
import os
import json
import uuid as uuid_lib

from ..tracing.tracer import SpanType
from .auth import CurrentUser, get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)

# fire-and-forget 后台任务引用集合：asyncio.create_task 只持弱引用，
# 不保存引用的任务可能执行中途被 GC，且异常静默丢失
_background_tasks: set = set()


def _spawn_background(coro, *, name: str) -> asyncio.Task:
    """创建带引用保存 + 完成回调记日志的后台任务（记忆提取等不阻塞响应的操作）"""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        if t.cancelled():
            logger.warning(f"后台任务 {name} 被取消")
            return
        exc = t.exception()
        if exc is not None:
            logger.warning(f"后台任务 {name} 失败: {exc}", exc_info=exc)

    task.add_done_callback(_on_done)
    return task

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

            # 反思教训存储（lesson）：与记忆系统解耦，独立生命周期
            # 用 service 层单例，保证与评测侧 reflector 操作同一个实例
            # （多实例并发写 JSONL 会互相覆盖）
            lesson_store = None
            if getattr(settings, "LESSON_INJECTION_ENABLED", True):
                try:
                    from ..services.lesson_store import get_lesson_store
                    lesson_store = get_lesson_store(config=settings, llm_client=llm_client)
                    logger.info("LessonStore初始化完成")
                except Exception as e:
                    logger.warning(f"LessonStore初始化失败: {e}")

            _deps = {
                "query_rewriter": rewriter,
                "citation_service": citation,
                "tracer": tracer,
                "hybrid_store": store,
                "llm_client": llm_client,
                "embedding_service": embedding_service,
                "reranker_service": reranker_service,
                "session_store": session_store,
                "memory_manager": memory_manager,
                "lesson_store": lesson_store,
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


def init_workflow_deps_provider() -> None:
    """把本模块的 deps 提供给 workflow 引擎

    workflow 的 LLM client / tracer / 检索能力全部复用 RAG 侧的单例，
    不另建一套连接池与模型客户端。
    """
    from ..workflows.engine import set_deps_provider
    set_deps_provider(get_deps)


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


ENTRY_SESSION_PREFIX = "entry-"
# 入园会话的原文留存期（秒）：证件号/手机号不长期留存在 Redis
ENTRY_SESSION_TTL = 2 * 60 * 60


def _derive_entry_action(question: str, session_id: str, slots: Optional[Dict],
                         force: bool = False) -> bool:
    """前置轻量意图闸：单一规则判定入口 + 显式强制 + 收紧的会话兜底。

    - force=True：司机在前端选择卡里本人点了「办理入园」，直接置 True。
    - 规则流水线（P0/P1/P2）是唯一判定来源，不再在外层做只读短路，
      否则句首办理意图永远进不来。
    - 会话兜底收紧：入园会话 / 已有字段只在「本轮不是只读咨询」时续留 Agent，
      避免闲聊、查规定被吞进入园流程。
    """
    from ..services.query_rewriter import QueryRewriter
    if force:
        return True
    if QueryRewriter.classify_entry_action(question or ""):
        return True
    in_entry_context = (
        str(session_id or "").startswith(ENTRY_SESSION_PREFIX) or bool(slots or {})
    )
    if in_entry_context and not QueryRewriter.is_entry_read_only(question or ""):
        return True
    return False


async def _prepare_entry_request(request: "ChatRequest", user: CurrentUser,
                                 session_id: str, entry_action: bool):
    """绑定请求级身份/字段上下文 + 会话归属 + 缩短留存期。"""
    from ..core.entry_context import bind as _bind_entry
    tokens = _bind_entry(
        user.username, session_id,
        slots=request.entry_slots or {},
        entry_action=entry_action,
        user_input=request.question,
    )
    if entry_action:
        deps = get_deps()
        store = deps.get("session_store")
        if store:
            try:
                await store.bind_session(session_id, user.username, ttl=ENTRY_SESSION_TTL)
            except Exception as e:
                logger.warning(f"绑定入园会话归属失败: {e}")
    return tokens


# ── 多流程配置化平台：后端权威意图路由（D10）──

FLOW_ACTION_STATES = ("action", "attach")


async def _resolve_flow(request: "ChatRequest", session_id: str,
                        user: CurrentUser) -> Dict[str, Any]:
    """判定只发生在后端：规则流水线 → flow_id + 动作；歧义下发候选卡。

    返回内部 decision（含 FlowDefinition 对象），不下发判定规则给前端。
    """
    from ..flows.registry import classify_text, get_flow_registry
    empty = {"flow_id": None, "action": "none", "needs_choice": False,
             "candidates": [], "rule_id": "none", "hint": "", "definition": None,
             "definition_version": 0}
    reg = get_flow_registry()
    try:
        await reg.load()
    except Exception as e:
        logger.warning(f"[flow_route] 注册表加载失败，降级到原链路: {e}")
        return empty
    definitions = reg.all_published()
    if not definitions:
        return empty

    # 用户在选择卡点了「咨询」：一次性标记生效，不再弹卡，也不挂办理工具
    if request.intent_resolved and request.flow_action == "readonly":
        return {"flow_id": request.flow_id, "action": "readonly", "needs_choice": False,
                "candidates": [], "rule_id": "user_choice_consult", "hint": "",
                "definition": None, "definition_version": 0}

    session_flow = None
    if request.flow_id:
        session_flow = request.flow_id
    else:
        for d in definitions:
            if str(session_id or "").startswith(d.session_prefix):
                session_flow = d.flow_id
                break
    force = bool(request.flow_force or request.entry_force)
    forced_id = request.flow_id or ("driver_entry" if request.entry_force else None)

    # 单据已提交并在途时，本会话该流程视为「已办结」：关闭 session_keep 兜底，
    # 后续闲聊 / 知识问答回到普通 RAG，不再每轮重出核对卡。显式办理意图仍由规则命中。
    session_completed = False
    if session_flow:
        try:
            from ..flows.service import has_active_application
            session_completed = await has_active_application(session_flow, user.username)
        except Exception as e:
            logger.warning(f"[flow_route] 在途单据查询失败，按未办结处理: {e}")
    decision = classify_text(
        request.question, definitions,
        session_flow_id=session_flow,
        forced_flow_id=forced_id if force else None,
        forced_action=request.flow_action or ("action" if force else None),
        intent_resolved=bool(request.intent_resolved or force),
        session_completed=session_completed,
    )
    decision.setdefault("candidates", [])
    fid = decision.get("flow_id")
    action = decision.get("action")
    definition = None
    version = 0
    if fid:
        target = next((d for d in definitions if d.flow_id == fid), None)
        if target is not None:
            version = int(request.definition_version or reg.published_version(fid) or 1)
            try:
                definition = await reg.resolve(
                    fid, username=user.username, role_id=user.role_id,
                    session_version=request.definition_version,
                )
            except Exception as e:
                logger.warning(f"[flow_route] 配置版本解析失败: {e}")
                definition = target
    # 办理动作：后端硬校验申请角色（force 不能绕过）
    if action in FLOW_ACTION_STATES and definition is not None:
        roles = definition.applicant_roles
        if roles and user.role_id != "admin" and user.role_id not in roles:
            decision = {**empty, "rule_id": "permission_denied",
                        "hint": "当前角色不能办理该流程，已切换为只读问答"}
    decision["definition"] = definition
    decision["definition_version"] = version
    return decision


async def _prepare_flow_request(request: "ChatRequest", user: CurrentUser,
                                session_id: str, decision: Dict[str, Any],
                                entry_action: bool):
    """绑定通用请求上下文 + 兼容旧入园上下文 + 会话归属 / TTL。"""
    from ..flows import context as fctx
    from ..core.entry_context import bind as _bind_entry
    slots = request.flow_slots or request.entry_slots or {}
    flow_tokens = fctx.bind(
        user.username, session_id, slots=slots,
        flow_id=decision.get("flow_id") or "", action=decision.get("action") or "",
        user_input=request.question, definition=decision.get("definition"),
        version=decision.get("definition_version") or 0,
        role_id=user.role_id,  # P0: 工具层据此做角色鉴权，防提示注入越权下单
    )
    entry_tokens = _bind_entry(user.username, session_id, slots=slots,
                               entry_action=entry_action, user_input=request.question)
    if decision.get("action") in FLOW_ACTION_STATES:
        deps = get_deps()
        store = deps.get("session_store")
        if store:
            try:
                hours = (decision.get("definition").ttl_hours
                         if decision.get("definition") else 2)
                await store.bind_session(session_id, user.username, ttl=hours * 3600)
            except Exception as e:
                logger.warning(f"绑定流程会话归属失败: {e}")
    return flow_tokens, entry_tokens


def _reset_flow(tokens) -> None:
    from ..flows import context as fctx
    from ..core.entry_context import reset as _reset_entry
    if not tokens:
        return
    flow_tokens, entry_tokens = tokens
    fctx.reset(flow_tokens)
    _reset_entry(entry_tokens)


class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题", min_length=1, max_length=2000)
    session_id: Optional[str] = Field(default=None, description="会话ID")
    stream: bool = Field(default=False, description="是否流式返回")
    use_agent: bool = Field(default=False, description="是否使用Agent模式（支持工具调用）")
    entry_slots: Optional[Dict[str, Any]] = Field(
        default=None,
        description="入园申请已抽字段；前端每轮回传，后端不落库",
    )
    entry_force: bool = Field(
        default=False,
        description="兼容旧前端：点「办理入园」时强制走入园 Agent",
    )
    # ── 多流程配置化平台（通用）──
    flow_id: Optional[str] = Field(default=None, description="用户在选择卡上点选的流程（后端权威校验）")
    flow_slots: Optional[Dict[str, Any]] = Field(default=None, description="通用槽位，每轮回传，不落库")
    flow_force: bool = Field(default=False, description="用户在选择卡上点「办理」，放宽歧义")
    flow_action: Optional[str] = Field(default=None, description="选择卡点「咨询」时传 readonly")
    intent_resolved: bool = Field(default=False, description="选择卡结果一次性标记，防死循环")
    definition_version: Optional[int] = Field(default=None, description="会话/单据锁定的配置版本")


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
    # 入园 Agent 的结构化产物：字段 / 缺失项 / 校验错误 / 确认卡
    entry_action: bool = False
    entry_slots: Optional[Dict[str, Any]] = None
    entry_payload: Optional[Dict[str, Any]] = None
    # 通用流程字段
    flow_id: Optional[str] = None
    flow_action: Optional[str] = None
    flow_slots: Optional[Dict[str, Any]] = None
    flow_payload: Optional[Dict[str, Any]] = None
    intent_choice: Optional[Dict[str, Any]] = None
    definition_version: int = 0
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


async def _build_scope_context(user: CurrentUser):
    """从当前用户解析出检索侧需要的权限上下文：
    - allowed_doc_ids: Milvus 检索时过滤的 doc_id 集合；None 表示 admin 看全部
    - user_visible_scopes: 用户可见的 scope 列表；None 表示 admin
    """
    from ..services import permission_service
    visible = await permission_service.get_visible_scopes(user.username)
    if visible is None:
        return None, None  # admin，不过滤
    allowed = await permission_service.get_accessible_doc_ids(user.username)
    return allowed, visible


@router.post("/query", response_model=ChatResponse, summary="知识库问答")
async def chat_query(request: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    task_id = str(uuid_lib.uuid4())[:8]
    session_id = request.session_id or str(uuid_lib.uuid4())[:8]
    allowed_doc_ids, user_visible_scopes = await _build_scope_context(user)

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

    entry_action = _derive_entry_action(
        request.question, session_id, request.entry_slots, force=request.entry_force
    )
    # 后端权威意图路由：规则流水线 / 候选卡 / 版本锁定
    decision = await _resolve_flow(request, session_id, user)
    flow_action = decision.get("action") if decision.get("action") in FLOW_ACTION_STATES else None
    flow_id = decision.get("flow_id") if (flow_action or decision.get("needs_choice")) else None
    if flow_action:
        entry_action = False  # 通用链路优先，避免双轨重复绑定

    # 两可句：不调模型、不跑检索，直接下发候选卡由用户本人点选
    if decision.get("needs_choice"):
        return ChatResponse(
            task_id=task_id,
            answer=decision.get("hint") or "请确认您要办理哪个流程",
            intent_choice={"candidates": decision.get("candidates") or [],
                           "hint": decision.get("hint") or "",
                           "question": request.question},
            flow_id=decision.get("flow_id"), flow_action="choice",
        )

    # 流程工具需要当前请求的真实身份，LLM 不可通过参数伪造；
    # contextvar 绑定到本次请求，finally 中必定清理。
    _flow_tokens = await _prepare_flow_request(request, user, session_id, decision, entry_action)

    try:
        from ..tracing.tracer import _mask_text
        if tracer:
            trace_id = tracer.start_trace(
                user_query=_mask_text(request.question),
                task_id=task_id,
                session_id=session_id,
            )

        initial_state = {
            "user_query": request.question,
            "session_id": session_id,
            "task_id": task_id,
            "allowed_doc_ids": allowed_doc_ids,
            "user_visible_scopes": user_visible_scopes,
            "denied_scope_hints": [],
            "optimized_query": None,
            "query_type": None,
            "intent": None,
            "keywords": [],
            "expanded_queries": [],
            "sub_queries": [],
            "query_vector": None,
            "raw_results": [],
            "filtered_results": [],
            "accumulated_results": [],
            "retrieval_confident": None,
            "need_more_evidence": False,
            "iteration_count": 0,
            "context": None,
            "answer": None,
            "citations": [],
            "error": None,
            "metadata": {},
            "tools_to_call": [],
            "tool_results": [],
            "entry_action": entry_action,
            "entry_slots": dict(request.entry_slots or {}),
            "entry_payload": {},
            "flow_id": flow_id,
            "flow_action": flow_action,
            "flow_definition_version": decision.get("definition_version") or 0,
            "needs_choice": False,
            "flow_slots": dict(request.flow_slots or request.entry_slots or {}),
            "flow_payload": {},
        }

        logger.info(f"[chat_query] initial_state user_query='{initial_state.get('user_query', '')}', user={user.username}, allowed_doc_ids={'all' if allowed_doc_ids is None else len(allowed_doc_ids)}")
        # 根 span：让后续所有节点 span 挂到它下面，形成真正的父子链路
        root_cm = (
            tracer.start_span(SpanType.REQUEST, "chat_query",
                              inputs={"question": _mask_text(request.question),
                                      "session_id": session_id,
                                      "entry_action": entry_action})
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
                status=_trace_status(result, answer_text),
            )

        # 保存对话到短期记忆；入园通道关闭长期记忆抽取（PII 不进全局记忆池）
        memory_manager = deps.get("memory_manager")
        if memory_manager:
            try:
                await memory_manager.save_turn(session_id, request.question, answer_text, citations_raw)
                if not entry_action and not flow_action:
                    _spawn_background(
                        memory_manager.extract_and_store(session_id, request.question, answer_text),
                        name=f"memory_extract_{session_id}",
                    )
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
            },
            entry_action=entry_action,
            entry_slots=result.get("entry_slots") or request.entry_slots,
            entry_payload=result.get("entry_payload") or None,
            flow_id=flow_id,
            flow_action=flow_action,
            flow_slots=result.get("flow_slots") or request.flow_slots or request.entry_slots,
            flow_payload=result.get("flow_payload") or None,
            definition_version=int(decision.get("definition_version") or 0),
        )

    except Exception as e:
        logger.error(f"聊天查询失败: {e}", exc_info=True)
        if tracer and trace_id:
            tracer.end_trace(
                trace_id=trace_id,
                status="error",
                error_message=str(e),
            )
        # P0: 不把内部异常文本回显给客户端（可能含 DSN / 密钥片段）
        return ChatResponse(
            task_id="error",
            answer="服务处理失败，请稍后重试",
            optimization={"original": request.question, "rewritten": request.question, "type": "error"}
        )
    finally:
        _reset_flow(_flow_tokens)


def _build_initial_state(
    question: str,
    session_id: str,
    task_id: str,
    allowed_doc_ids=None,
    user_visible_scopes=None,
    entry_action: bool = False,
    entry_slots: Optional[Dict[str, Any]] = None,
    flow_id: Optional[str] = None,
    flow_action: Optional[str] = None,
    flow_slots: Optional[Dict[str, Any]] = None,
    flow_definition_version: int = 0,
) -> Dict:
    return {
        "user_query": question,
        "session_id": session_id,
        "task_id": task_id,
        "allowed_doc_ids": allowed_doc_ids,
        "user_visible_scopes": user_visible_scopes,
        "denied_scope_hints": [],
        "optimized_query": None,
        "query_type": None,
        "intent": None,
        "keywords": [],
        "expanded_queries": [],
        "sub_queries": [],
        "query_vector": None,
        "raw_results": [],
        "filtered_results": [],
        "accumulated_results": [],
        "retrieval_confident": None,
        "need_more_evidence": False,
        "iteration_count": 0,
        "context": None,
        "answer": None,
        "citations": [],
        "error": None,
        "metadata": {},
        "tools_to_call": [],
        "tool_results": [],
        "entry_action": entry_action,
        "entry_slots": dict(entry_slots or {}),
        "entry_payload": {},
        "flow_id": flow_id,
        "flow_action": flow_action,
        "flow_definition_version": flow_definition_version,
        "needs_choice": False,
        "flow_slots": dict(flow_slots or entry_slots or {}),
        "flow_payload": {},
    }


_DEGRADED_ANSWERS = (
    "生成回答时出现错误", "LLM服务未配置", "服务处理失败", "无法生成回答",
)


def _trace_status(result: Dict, answer: str) -> str:
    """图节点把检索/生成失败吞成降级文案返回，不能一律记 ok。"""
    if result.get("error") or any(marker in (answer or "") for marker in _DEGRADED_ANSWERS):
        return "error"
    return "ok"


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
async def chat_query_stream(request: ChatRequest, user: CurrentUser = Depends(get_current_user)):
    task_id = str(uuid_lib.uuid4())[:8]
    session_id = request.session_id or str(uuid_lib.uuid4())[:8]
    allowed_doc_ids, user_visible_scopes = await _build_scope_context(user)

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

    entry_action = _derive_entry_action(
        request.question, session_id, request.entry_slots, force=request.entry_force
    )
    decision = await _resolve_flow(request, session_id, user)
    flow_action = decision.get("action") if decision.get("action") in FLOW_ACTION_STATES else None
    flow_id = decision.get("flow_id") if (flow_action or decision.get("needs_choice")) else None
    if flow_action:
        entry_action = False

    token_queue: asyncio.Queue = asyncio.Queue()
    graph_error: Exception | None = None  # 后台图异常经此传给 SSE 生成器（P0: 防止异常被吞 + 流挂死）
    graph_task: asyncio.Task | None = None  # 外层持有引用，客户端断开时可取消

    async def _run_graph(initial_state, accumulated_state):
        """后台协程：执行 graph，收集步骤事件；无论成功/失败/取消都必须放哨兵"""
        nonlocal graph_error
        from ..agents.graph import extract_span_outputs
        step_events = []
        from ..tracing.tracer import _mask_text
        root_cm = (
            tracer.start_span(SpanType.REQUEST, "chat_query_stream",
                              inputs={"question": _mask_text(request.question),
                                      "session_id": session_id,
                                      "entry_action": entry_action})
            if tracer else contextlib.nullcontext()
        )
        try:
            with root_cm as root_span:
                if root_span is not None:
                    root_span.set_attribute("includes_sse_flush", True)
                async for chunk in graph.astream(initial_state):
                    for node_name, updates in chunk.items():
                        accumulated_state.update(updates)
                        logger.info(f"[run_graph] 节点完成: {node_name}")
                        step_data = extract_span_outputs(node_name, updates, accumulated_state)
                        step_events.append({
                            "type": "step",
                            "node": node_name,
                            "status": "completed",
                            "data": step_data,
                        })
        except asyncio.CancelledError:
            # 客户端断开/服务关停导致的取消：正常传播，不伪装成业务错误
            raise
        except Exception as e:
            graph_error = e
            logger.error(f"[run_graph] 图执行失败: {e}", exc_info=True)
        finally:
            # P0: 哨兵必须在所有路径写入，否则 event_generator 会永久阻塞在 queue.get()
            token_queue.put_nowait(None)
        logger.info(f"[run_graph] graph 执行完成, 共 {len(step_events)} 个步骤")
        return step_events

    async def event_generator():
        nonlocal trace_id, graph_task
        # 两可句：后端下发候选，前端只渲染；不调模型、不跑检索、不写库
        if decision.get("needs_choice"):
            choice = {
                "type": "intent_choice",
                "candidates": decision.get("candidates") or [],
                "hint": decision.get("hint") or "请确认您要办理哪个流程",
                "question": request.question,
                "flow_id": decision.get("flow_id"),
            }
            yield f"data: {json.dumps(choice, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'task_id': task_id, 'answer': choice['hint'], 'citations': [], 'intent_choice': choice, 'flow_action': 'choice', 'flow_id': choice['flow_id']}, ensure_ascii=False)}\n\n"
            return
        _flow_tokens = await _prepare_flow_request(request, user, session_id, decision, entry_action)
        try:
            from ..tracing.tracer import _mask_text
            if tracer:
                trace_id = tracer.start_trace(
                    user_query=_mask_text(request.question),
                    task_id=task_id,
                    session_id=session_id,
                )

            initial_state = _build_initial_state(
                request.question, session_id, task_id,
                allowed_doc_ids=allowed_doc_ids,
                user_visible_scopes=user_visible_scopes,
                entry_action=entry_action,
                entry_slots=request.entry_slots,
                flow_id=flow_id,
                flow_action=flow_action,
                flow_slots=request.flow_slots or request.entry_slots,
                flow_definition_version=decision.get("definition_version") or 0,
            )
            initial_state["_token_queue"] = token_queue
            accumulated_state = dict(initial_state)

            # graph 后台执行，token 并发消费
            graph_task = asyncio.create_task(_run_graph(initial_state, accumulated_state))
            token_count = 0

            while True:
                token = await token_queue.get()
                if token is None:
                    logger.info(f"[stream] token 流结束, 共发送 {token_count} tokens")
                    break
                token_count += 1
                if token_count <= 3:
                    logger.info(f"[stream] token #{token_count}: {token!r}")
                yield f"data: {json.dumps({'type': 'token', 'content': token}, ensure_ascii=False)}\n\n"

            # 等后台图真正结束；图内异常已从 graph_error 带出，转成 SSE error 事件
            step_events = await graph_task
            if graph_error is not None:
                raise graph_error

            # 发送步骤事件（在所有 token 之后）
            for event in step_events:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            answer_text = accumulated_state.get("answer", "无法生成回答")
            citations_raw = accumulated_state.get("citations", [])
            citations = _format_citations(citations_raw)

            if tracer and trace_id:
                tracer.end_trace(
                    trace_id=trace_id,
                    answer=answer_text,
                    sources_count=len(citations_raw),
                    status=_trace_status(accumulated_state, answer_text),
                )

            # 保存对话到短期记忆；入园通道关闭长期记忆抽取（PII 不进全局记忆池）
            memory_manager = deps.get("memory_manager")
            if memory_manager:
                try:
                    await memory_manager.save_turn(session_id, request.question, answer_text, citations_raw)
                    if not entry_action and not flow_action:
                        _spawn_background(
                            memory_manager.extract_and_store(session_id, request.question, answer_text),
                            name=f"memory_extract_{session_id}",
                        )
                except Exception as e:
                    logger.warning(f"记忆保存失败: {e}")

            entry_payload = accumulated_state.get("entry_payload") or None
            flow_payload = accumulated_state.get("flow_payload") or entry_payload
            # 通用流程 Agent 的结构化结果单独推事件：
            # 前端可在 done 之前先把 slots / 确认卡落到会话状态里。
            if flow_action and flow_payload:
                yield f"data: {json.dumps({'type': 'flow_payload', 'payload': flow_payload, 'flow_id': flow_id}, ensure_ascii=False)}\n\n"
                # 兼容旧前端
                yield f"data: {json.dumps({'type': 'entry_payload', 'payload': flow_payload}, ensure_ascii=False)}\n\n"

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
                "entry_action": entry_action,
                "entry_slots": accumulated_state.get("entry_slots") or request.entry_slots,
                "entry_payload": entry_payload,
                "flow_id": flow_id,
                "flow_action": flow_action,
                "flow_slots": accumulated_state.get("flow_slots") or request.flow_slots or request.entry_slots,
                "flow_payload": flow_payload,
                "definition_version": decision.get("definition_version") or 0,
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.error(f"SSE聊天查询失败: {e}", exc_info=True)
            if tracer and trace_id:
                tracer.end_trace(trace_id=trace_id, status="error", error_message=str(e))
            # P0: 不把内部异常文本回显给客户端，只给固定文案 + trace_id 便于排查
            yield f"data: {json.dumps({'type': 'error', 'message': '服务处理失败，请稍后重试', 'trace_id': trace_id}, ensure_ascii=False)}\n\n"
        finally:
            # P0: 客户端断开（GeneratorExit/CancelledError）时取消后台图，避免继续下单/写记忆/烧 token
            if graph_task is not None and not graph_task.done():
                graph_task.cancel()
                logger.info("[stream] 客户端断开或服务关闭，已取消后台 graph 任务")
            _reset_flow(_flow_tokens)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def _assert_session_owner(session_id: str, user: CurrentUser, store) -> None:
    """会话归属校验：任何登录用户不能凭 session_id 读/删别人的对话。

    旧会话没有 owner 记录，除 admin 外一律拒绝（fail-closed）。
    """
    owner = await store.get_owner(session_id)
    if user.role_id != "admin" and (not owner or owner != user.username):
        raise HTTPException(status_code=403, detail="无权访问该会话")


@router.get("/history/{session_id}", summary="获取聊天历史")
async def get_chat_history(
    session_id: str,
    limit: int = 60,
    user: CurrentUser = Depends(get_current_user),
):
    deps = get_deps()
    session_store = deps.get("session_store")
    if not session_store:
        return {"session_id": session_id, "history": []}
    try:
        await _assert_session_owner(session_id, user, session_store)
        messages = await session_store.get_messages(session_id, limit=limit)
        return {"session_id": session_id, "history": messages}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取聊天历史失败: {e}")
        return {"session_id": session_id, "history": []}


@router.delete("/history/{session_id}", summary="清空聊天历史")
async def clear_chat_history(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    deps = get_deps()
    session_store = deps.get("session_store")
    if session_store:
        try:
            await _assert_session_owner(session_id, user, session_store)
            await session_store.clear_session(session_id)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"清空聊天历史失败: {e}")
    return {"message": "历史已清空", "session_id": session_id}


@router.get("/sessions", summary="获取会话列表")
async def list_sessions(
    limit: int = 50,
    user: CurrentUser = Depends(get_current_user),
):
    """只列本人会话；admin 可看全部，响应里带 owner 便于审计。"""
    deps = get_deps()
    session_store = deps.get("session_store")
    if not session_store:
        return {"sessions": []}
    try:
        owner = None if user.role_id == "admin" else user.username
        sessions = await session_store.list_sessions(limit=limit, owner=owner)
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
