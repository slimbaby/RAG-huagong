"""
入园预约请求级上下文（方案乙）

申请人身份、会话 ID、以及「前端回传的已抽字段」都由 chat API 在请求入口
写入 contextvar。工具只从此处读取，LLM 无法通过参数伪造身份，也不必在多轮
对话里反复搬运一大包 PII 字段（减少幻觉、减少 tool_call 日志泄露面）。
"""

from contextvars import ContextVar, Token
from typing import Any, Dict, Optional, Tuple

current_username: ContextVar[str] = ContextVar("entry_current_username", default="")
current_session_id: ContextVar[str] = ContextVar("entry_current_session_id", default="")
current_slots: ContextVar[Dict[str, Any]] = ContextVar("entry_current_slots", default={})
current_entry_action: ContextVar[bool] = ContextVar("entry_current_entry_action", default=False)
current_user_input: ContextVar[str] = ContextVar("entry_current_user_input", default="")
# 单轮内工具产出的结构化结果，generate_node 从这里取回写进 state
current_payload: ContextVar[Dict[str, Any]] = ContextVar("entry_current_payload", default={})


def bind(username: str, session_id: str = "", slots: Optional[Dict[str, Any]] = None,
         entry_action: bool = False, user_input: str = "") -> Tuple[Token, ...]:
    """绑定当前请求上下文，返回 reset 所需的 token 元组。"""
    return (
        current_username.set(username or ""),
        current_session_id.set(session_id or ""),
        current_slots.set(dict(slots or {})),
        current_entry_action.set(bool(entry_action)),
        current_user_input.set(user_input or ""),
        current_payload.set({}),
    )


def reset(tokens: Optional[Tuple[Token, ...]]) -> None:
    if not tokens:
        return
    variables = (current_username, current_session_id, current_slots,
                 current_entry_action, current_user_input, current_payload)
    for var, token in zip(variables, tokens):
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


def is_entry_action() -> bool:
    return bool(current_entry_action.get(False))


def user_input() -> str:
    return current_user_input.get("")


def set_payload(payload: Dict[str, Any]) -> None:
    """工具把结构化结果暂存到请求上下文，generate_node 取用。"""
    current_payload.set(dict(payload or {}))


def get_payload() -> Dict[str, Any]:
    return dict(current_payload.get({}) or {})
