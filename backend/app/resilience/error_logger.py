"""
错误记录器 - 分类、分级、去重
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import hashlib
import json
import os
import logging

logger = logging.getLogger(__name__)


class ErrorSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorCategory(str, Enum):
    TIMEOUT = "timeout"
    ITERATION_LIMIT = "iteration_limit"
    CIRCUIT_BREAKER = "circuit_breaker"
    LLM_ERROR = "llm_error"
    RETRIEVAL_ERROR = "retrieval_error"
    PARSING_ERROR = "parsing_error"
    VALIDATION_ERROR = "validation_error"
    EXTERNAL_SERVICE = "external_service"
    UNKNOWN = "unknown"


@dataclass
class ErrorRecord:
    error_id: str
    timestamp: str
    severity: str
    category: str
    error_type: str
    error_message: str
    error_traceback: str
    context: Dict
    request_info: Dict
    execution_info: Dict
    circuit_breaker_state: Optional[str]
    diagnostics: Dict
    resolved: bool = False
    resolved_at: Optional[str] = None
    resolved_notes: Optional[str] = None
    fingerprint: str = ""


class ErrorLogger:
    """错误记录器"""

    def __init__(self, storage_path: str = "./logs/errors"):
        self.storage_path = storage_path
        self._recent_errors: List[ErrorRecord] = []
        self._fingerprint_counts: Dict[str, int] = {}
        os.makedirs(storage_path, exist_ok=True)

    def log_error(self, error: Exception, severity: ErrorSeverity = ErrorSeverity.MEDIUM, category: ErrorCategory = ErrorCategory.UNKNOWN, trace_id: str = None, task_id: str = None, session_id: str = None, user_query: str = None, operation: str = "", **kwargs) -> ErrorRecord:
        import traceback
        error_type = type(error).__name__
        error_message = str(error)
        error_traceback = traceback.format_exc()
        fingerprint = hashlib.md5(f"{error_type}:{category.value}:{error_message}:{operation}".encode()).hexdigest()[:16]

        self._fingerprint_counts[fingerprint] = self._fingerprint_counts.get(fingerprint, 0) + 1

        record = ErrorRecord(
            error_id=hashlib.md5(f"{time.time()}:{error_message}".encode()).hexdigest()[:16],
            timestamp=datetime.now().isoformat(),
            severity=severity.value,
            category=category.value,
            error_type=error_type,
            error_message=error_message[:1000],
            error_traceback=error_traceback[:5000],
            context={"trace_id": trace_id, "task_id": task_id, "session_id": session_id, "user_query": user_query},
            request_info={},
            execution_info={"operation": operation},
            circuit_breaker_state=kwargs.get("circuit_breaker_state"),
            diagnostics={},
            fingerprint=fingerprint,
        )

        self._recent_errors.append(record)
        if len(self._recent_errors) > 1000:
            self._recent_errors = self._recent_errors[-500:]

        self._persist_error(record)
        return record

    def _persist_error(self, record: ErrorRecord):
        try:
            date_str = datetime.now().strftime("%Y-%m-%d")
            file_path = os.path.join(self.storage_path, f"errors_{date_str}.jsonl")
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record.__dict__, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"Failed to persist error: {e}")

    def get_recent_errors(self, limit: int = 50, severity: str = None, category: str = None, resolved: bool = None) -> List[ErrorRecord]:
        errors = self._recent_errors
        if severity:
            errors = [e for e in errors if e.severity == severity]
        if category:
            errors = [e for e in errors if e.category == category]
        if resolved is not None:
            errors = [e for e in errors if e.resolved == resolved]
        return errors[-limit:]

    def get_error_by_id(self, error_id: str) -> Optional[ErrorRecord]:
        for e in self._recent_errors:
            if e.error_id == error_id:
                return e
        return None

    def get_errors_by_trace(self, trace_id: str) -> List[ErrorRecord]:
        return [e for e in self._recent_errors if e.context.get("trace_id") == trace_id]

    def get_errors_by_task(self, task_id: str) -> List[ErrorRecord]:
        return [e for e in self._recent_errors if e.context.get("task_id") == task_id]

    def get_error_statistics(self, hours: int = 24) -> Dict:
        from collections import Counter
        severity_counts = Counter(e.severity for e in self._recent_errors)
        category_counts = Counter(e.category for e in self._recent_errors)
        unresolved = sum(1 for e in self._recent_errors if not e.resolved)
        return {"total_errors": len(self._recent_errors), "by_severity": dict(severity_counts), "by_category": dict(category_counts), "unresolved_count": unresolved, "top_fingerprints": sorted(self._fingerprint_counts.items(), key=lambda x: x[1], reverse=True)[:10]}

    def resolve_error(self, error_id: str, notes: str = "") -> bool:
        for e in self._recent_errors:
            if e.error_id == error_id:
                e.resolved = True
                e.resolved_at = datetime.now().isoformat()
                e.resolved_notes = notes
                return True
        return False

    def search_errors(self, query: str = "", error_type: str = "", limit: int = 50) -> List[ErrorRecord]:
        errors = self._recent_errors
        if error_type:
            errors = [e for e in errors if e.error_type == error_type]
        if query:
            errors = [e for e in errors if query.lower() in e.error_message.lower()]
        return errors[-limit:]
