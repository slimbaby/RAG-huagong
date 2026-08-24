"""
日志上下文变量 - 通过 contextvars 在请求链路中传播 request_id / trace_id / task_id
"""

import contextvars

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")
task_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("task_id", default="")
