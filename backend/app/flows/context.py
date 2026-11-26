"""
多流程请求级上下文（entry_context 的泛化）

身份 / 会话 / 槽位 / 当前流程定义 / 锁定版本都由 chat API 在请求入口写入 contextvar；
模型无法通过参数伪造身份，也不必在多轮里反复搬运一整包 PII 字段。
"""

from contextvars import ContextVar, Token
from typing import Any, Dict, Optional, Tuple

from .model import FlowDefinition

current_username: ContextVar[str] = ContextVar("flow_current_username", default="")
current_session_id: ContextVar[str] = ContextVar("flow_current_session_id", default="")
current_slots: ContextVar[Dict[str, Any]] = ContextVar("flow_current_slots", default={})
current_flow_id: ContextVar[str] = ContextVar("flow_current_flow_id", default="")
current_action: ContextVar[str] = ContextVar("flow_current_action", default="")
current_user_input: ContextVar[str] = ContextVar("flow_current_user_input", default="")
current_definition: ContextVar[Optional[FlowDefinition]] = ContextVar("flow_current_definition", default=None)
current_version: ContextVar[int] = ContextVar("flow_current_version", default=0)
current_payload: ContextVar[Dict[str, Any]] = ContextVar("flow_current_payload", default={})

_VARS = (
    current_username, current_session_id, current_slots, current_flow_id,
    current_action, current_user_input, current_definition, current_version, current_payload,
)


def bind(username: str, session_id: str = "", *, slots: Optional[Dict[str, Any]] = None,
         flow_id: str = "", action: str = "", user_input: str = "",
         definition: Optional[FlowDefinition] = None, version: int = 0) -> Tuple[Token, ...]:
    return (
        current_username.set(username or ""),
        current_session_id.set(session_id or ""),
        current_slots.set(dict(slots or {})),
        current_flow_id.set(flow_id or ""),
        current_action.set(action or ""),
        current_user_input.set(user_input or ""),
        current_definition.set(definition),
        current_version.set(int(version or 0)),
        current_payload.set({}),
    )


def reset(tokens: Optional[Tuple[Token, ...]]) -> None:
    if not tokens:
        return
    for var, token in zip(_VARS, tokens):
        try:
            var.reset(token)
        except (ValueError, TypeError):
            pass


def username() -> str:
    return current_username.get("")


def session_id() -> str:
    return current_session_id.get("")


def slots() -> Dict[str, Any]:
    return dict(current_slots.get({}) or {})


def flow_id() -> str:
    return current_flow_id.get("")


def action() -> str:
    return current_action.get("")


def user_input() -> str:
    return current_user_input.get("")


def definition() -> Optional[FlowDefinition]:
    return current_definition.get(None)


def version() -> int:
    return int(current_version.get(0) or 0)


def set_payload(payload: Dict[str, Any]) -> None:
    current_payload.set(dict(payload or {}))


def get_payload() -> Dict[str, Any]:
    return dict(current_payload.get({}) or {})
