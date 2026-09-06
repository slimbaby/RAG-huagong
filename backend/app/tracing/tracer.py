"""
链路追踪器 - 全链路可观测性
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import contextvars
import atexit
import json
import os
import uuid
import time
import logging

logger = logging.getLogger(__name__)

# 上下文变量：按 asyncio Task 隔离，避免并发请求互相覆盖 trace_id / span 栈
# 用不可变 tuple 存 span 栈，子 Task 里的 push/pop 不会污染父 Task
_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("rag_trace_id", default="")
_span_stack_var: contextvars.ContextVar[Tuple[str, ...]] = contextvars.ContextVar("rag_span_stack", default=())


class SpanType(str, Enum):
    REQUEST = "request"
    QUERY_OPTIMIZE = "query_optimize"
    RETRIEVAL = "retrieval"
    VECTOR_SEARCH = "vector_search"
    BM25_SEARCH = "bm25_search"
    HYBRID_FUSION = "hybrid_fusion"
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    CHECKPOINT = "checkpoint"


@dataclass
class Span:
    span_id: str
    trace_id: str
    parent_span_id: Optional[str]
    span_type: str
    operation_name: str
    start_time: float
    depth: int = 0
    end_time: float = 0
    duration_ms: float = 0
    status: str = "ok"
    error_message: str = ""
    inputs: Dict = field(default_factory=dict)
    outputs: Dict = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)
    events: List[Dict] = field(default_factory=list)

    def add_event(self, name: str, attributes: Dict = None):
        self.events.append({"name": name, "attributes": attributes or {}, "time": time.time()})

    def set_attribute(self, key: str, value: Any):
        self.metadata[key] = value

    def to_dict(self) -> Dict:
        return self.__dict__


@dataclass
class Trace:
    trace_id: str
    task_id: Optional[str]
    request_id: Optional[str]
    user_query: str
    optimized_query: str = ""
    session_id: str = ""
    start_time: float = 0
    end_time: float = 0
    duration_ms: float = 0
    status: str = "ok"
    error_message: str = ""
    answer: str = ""
    sources_count: int = 0
    tokens_used: Dict = field(default_factory=lambda: {"prompt": 0, "completion": 0, "total": 0})
    spans: List[Span] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class DummySpan:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def add_event(self, *args, **kwargs): pass
    def set_attribute(self, *args, **kwargs): pass


class Tracer:
    """全链路追踪器"""

    def __init__(self, storage_path: str = "./logs/traces"):
        self.storage_path = storage_path
        self._active_traces: Dict[str, Trace] = {}
        self._active_spans: Dict[str, Span] = {}
        self._completed_spans: Dict[str, List[Span]] = {}  # trace_id -> completed spans
        self._ctx_tokens: Dict[str, Any] = {}  # trace_id -> ContextVar token（回滚用）
        self._log_tokens: Dict[str, Any] = {}
        self._history: List[Trace] = []
        self._max_history = 1000
        os.makedirs(storage_path, exist_ok=True)
        # 落盘挪到独立线程，避免同步 I/O 阻塞 event loop
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trace-persist")
        atexit.register(self._shutdown_executor)

    def _shutdown_executor(self):
        try:
            self._executor.shutdown(wait=True)
        except Exception:
            pass

    def start_trace(self, user_query: str, request_id: str = None, task_id: str = None, session_id: str = None, metadata: Dict = None) -> str:
        trace_id = str(uuid.uuid4())[:8]
        trace = Trace(trace_id=trace_id, task_id=task_id, request_id=request_id, user_query=user_query, session_id=session_id or "", start_time=time.time(), metadata=metadata or {})
        self._active_traces[trace_id] = trace
        self._completed_spans[trace_id] = []
        # 记录 token 以便 end_trace 精确回滚，避免并发场景下残留脏上下文
        trace_token = _trace_id_var.set(trace_id)
        self._ctx_tokens[trace_id] = trace_token
        # 将 trace_id 写入日志上下文
        log_token = None
        try:
            from ..core.logging_context import trace_id_var
            log_token = trace_id_var.set(trace_id)
        except Exception:
            pass
        self._log_tokens[trace_id] = log_token
        return trace_id

    def end_trace(self, trace_id: str, answer: str = "", sources_count: int = 0, status: str = "ok", error_message: str = ""):
        trace = self._active_traces.pop(trace_id, None)
        if not trace:
            return
        # 回滚上下文：只影响当前 Task，不会波及其他并发请求
        trace_token = self._ctx_tokens.pop(trace_id, None)
        if trace_token is not None:
            try:
                _trace_id_var.reset(trace_token)
            except ValueError:
                _trace_id_var.set("")
        log_token = self._log_tokens.pop(trace_id, None)
        if log_token is not None:
            try:
                from ..core.logging_context import trace_id_var
                trace_id_var.reset(log_token)
            except Exception:
                pass
        trace.end_time = time.time()
        trace.duration_ms = (trace.end_time - trace.start_time) * 1000
        trace.answer = answer[:2000] if answer else ""
        trace.sources_count = sources_count
        trace.status = status
        trace.error_message = error_message
        trace.spans = self._completed_spans.pop(trace_id, [])
        self._history.append(trace)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        # trace 已从 active 移除并冻结，交给后台线程落盘
        try:
            self._executor.submit(self._persist_trace, trace)
        except RuntimeError:
            # executor 已关闭（进程退出中），降级为同步落盘，保证数据不丢
            self._persist_trace(trace)

    @contextmanager
    def start_span(self, span_type: SpanType, operation_name: str, inputs: Dict = None):
        trace_id = _trace_id_var.get()
        stack = _span_stack_var.get()
        parent_span_id = stack[-1] if stack else None
        span_id = str(uuid.uuid4())[:8]
        span = Span(
            span_id=span_id,
            trace_id=trace_id or "",
            parent_span_id=parent_span_id,
            span_type=span_type.value,
            operation_name=operation_name,
            start_time=time.time(),
            depth=len(stack),
            inputs=inputs or {},
        )
        self._active_spans[span_id] = span
        token = _span_stack_var.set(stack + (span_id,))
        try:
            yield span
        except Exception as e:
            span.status = "error"
            span.error_message = str(e)
            raise
        finally:
            span.end_time = time.time()
            span.duration_ms = (span.end_time - span.start_time) * 1000
            self._active_spans.pop(span_id, None)
            _span_stack_var.reset(token)
            if trace_id and trace_id in self._completed_spans:
                self._completed_spans[trace_id].append(span)

    def add_event(self, name: str, attributes: Dict = None):
        pass

    def set_attribute(self, key: str, value: Any):
        pass

    def record_tokens(self, prompt_tokens: int, completion_tokens: int):
        trace_id = _trace_id_var.get()
        if trace_id and trace_id in self._active_traces:
            t = self._active_traces[trace_id]
            t.tokens_used["prompt"] += prompt_tokens
            t.tokens_used["completion"] += completion_tokens
            t.tokens_used["total"] += prompt_tokens + completion_tokens

    def get_trace(self, trace_id: str) -> Optional[Trace]:
        if trace_id in self._active_traces:
            return self._active_traces[trace_id]
        for t in self._history:
            if t.trace_id == trace_id:
                return t
        return self._load_trace(trace_id)

    def get_trace_by_task(self, task_id: str) -> Optional[Trace]:
        for t in self._history:
            if t.task_id == task_id:
                return t
        return None

    def search_traces(self, session_id: str = None, status: str = None, limit: int = 50) -> List[Trace]:
        results = self._history
        if session_id:
            results = [t for t in results if t.session_id == session_id]
        if status:
            results = [t for t in results if t.status == status]
        return results[-limit:]

    def _persist_trace(self, trace: Trace):
        try:
            # 摘要行（轻量字段）：写入每日索引文件，供列表接口快速加载
            summary = {k: v for k, v in trace.__dict__.items() if k != "spans"}
            summary["answer"] = (trace.answer or "")[:200]
            date_str = datetime.now().strftime("%Y-%m-%d")
            file_path = os.path.join(self.storage_path, f"traces_{date_str}.jsonl")
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(summary, ensure_ascii=False, default=str) + "\n")
            # 完整数据（含 spans）：单独落盘，详情/火焰图按 trace_id O(1) 直读
            details_dir = os.path.join(self.storage_path, "details")
            os.makedirs(details_dir, exist_ok=True)
            detail = dict(trace.__dict__)
            detail["spans"] = [s.to_dict() if hasattr(s, "to_dict") else s for s in trace.spans]
            detail_path = os.path.join(details_dir, f"{trace.trace_id}.json")
            with open(detail_path, "w", encoding="utf-8") as f:
                json.dump(detail, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.error(f"Failed to persist trace: {e}")

    def load_detail_file(self, trace_id: str) -> Optional[Dict]:
        """直接读取单条 trace 的完整详情文件（新格式）"""
        detail_path = os.path.join(self.storage_path, "details", f"{trace_id}.json")
        if not os.path.exists(detail_path):
            return None
        try:
            with open(detail_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load trace detail {trace_id}: {e}")
            return None

    def _load_trace(self, trace_id: str) -> Optional[Dict]:
        # 新格式：O(1) 直读详情文件
        detail = self.load_detail_file(trace_id)
        if detail is not None:
            return detail
        # 兼容旧格式（摘要与详情混在大文件里）：全量扫描兜底
        try:
            for filename in os.listdir(self.storage_path):
                if filename.startswith("traces_") and filename.endswith(".jsonl"):
                    file_path = os.path.join(self.storage_path, filename)
                    with open(file_path, "r", encoding="utf-8") as f:
                        for line in f:
                            data = json.loads(line)
                            if data.get("trace_id") == trace_id:
                                return data
        except Exception:
            pass
        return None
