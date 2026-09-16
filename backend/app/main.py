"""
化工园区RAG知识库系统 - FastAPI主入口 (LangGraph版)
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import logging
import logging.handlers
import json
import time
import uuid
import os
import sys

os.makedirs("./logs", exist_ok=True)
os.makedirs("./checkpoints", exist_ok=True)


class JSONFormatter(logging.Formatter):
    """结构化 JSON 日志格式，从 contextvars 自动注入请求上下文"""

    def format(self, record):
        from .core.logging_context import request_id_var, trace_id_var, task_id_var

        log_obj = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }

        req_id = request_id_var.get("")
        tr_id = trace_id_var.get("")
        tk_id = task_id_var.get("")
        if req_id:
            log_obj["request_id"] = req_id
        if tr_id:
            log_obj["trace_id"] = tr_id
        if tk_id:
            log_obj["task_id"] = tk_id

        if record.exc_info and record.exc_info[0]:
            log_obj["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_obj, ensure_ascii=False)


# 延迟读取配置（config 可能尚未加载）
def _get_log_level() -> int:
    try:
        from .core.config import settings
        if settings.APP_DEBUG:
            return logging.DEBUG
        return getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    except Exception:
        return logging.INFO


def _get_log_config():
    try:
        from .core.config import settings
        return settings.LOG_MAX_BYTES, settings.LOG_BACKUP_COUNT
    except Exception:
        return 10 * 1024 * 1024, 5


text_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_level = _get_log_level()
max_bytes, backup_count = _get_log_config()

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(text_formatter)
console_handler.setLevel(log_level)

file_handler = logging.handlers.RotatingFileHandler(
    "./logs/app.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
)
file_handler.setFormatter(text_formatter)
file_handler.setLevel(log_level)

json_handler = logging.handlers.RotatingFileHandler(
    "./logs/app.jsonl", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
)
json_handler.setFormatter(JSONFormatter())
json_handler.setLevel(log_level)

root_logger = logging.getLogger()
root_logger.setLevel(log_level)
root_logger.addHandler(console_handler)
root_logger.addHandler(file_handler)
root_logger.addHandler(json_handler)

# 第三方库日志降噪：openai/httpx 的 DEBUG 日志会记录完整请求体（含对话内容和密钥头），
# 无论 APP_DEBUG 是否开启都强制压制到 WARNING，防止敏感信息落盘
for _noisy in ("openai", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logger.info("日志系统初始化完成")

app = FastAPI(
    title="化工园区RAG知识库系统 (LangGraph版)",
    description="基于LangGraph Agent的化工园区RAG知识库系统，支持多格式文档上传、混合检索、智能问答、多Agent协作",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

from .core.config import settings

# CORS 白名单：来自配置（.env 的 CORS_ORIGINS），禁止对任意来源开放带凭证访问
_cors_origins = [o for o in settings.CORS_ORIGINS if o] or ["http://localhost:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    from .core.logging_context import request_id_var
    request_id = str(uuid.uuid4())[:8]
    token = request_id_var.set(request_id)
    start_time = time.time()
    logger.info(f"请求开始: {request.method} {request.url.path}")
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        response.headers["X-Process-Time"] = f"{process_time:.3f}"
        response.headers["X-Request-ID"] = request_id
        logger.info(f"请求完成: {request.method} {request.url.path} - {response.status_code} ({process_time:.3f}s)")
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"请求异常: {request.method} {request.url.path} - {str(e)} ({process_time:.3f}s)", exc_info=True)
        raise
    finally:
        request_id_var.reset(token)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(status_code=500, content={"error": "Internal Server Error", "message": str(exc) if settings.APP_DEBUG else "服务器内部错误"})


@app.get("/health", tags=["系统"])
async def health_check():
    return {"status": "healthy", "service": settings.APP_NAME, "version": "2.0.0"}


from .api import auth, chat, documents, health, resilience, errors, tracing, checkpoints, evaluation, scopes
from .core.security import verify_token
from fastapi import Depends

# 除认证和健康检查外，所有 API 路由都要求 Bearer JWT（AUTH_ENABLED=False 时自动放行）
_protected = [Depends(verify_token)]

app.include_router(health.router, prefix="/api", tags=["健康检查"])
app.include_router(auth.router, prefix="/api/auth", tags=["认证"])
app.include_router(chat.router, prefix="/api/chat", tags=["聊天"], dependencies=_protected)
app.include_router(documents.router, prefix="/api/documents", tags=["文档管理"], dependencies=_protected)
app.include_router(resilience.router, prefix="/api/resilience", tags=["韧性保护"], dependencies=_protected)
app.include_router(errors.router, prefix="/api/errors", tags=["错误记录"], dependencies=_protected)
app.include_router(tracing.router, prefix="/api/tracing", tags=["链路追踪"], dependencies=_protected)
app.include_router(checkpoints.router, prefix="/api/checkpoints", tags=["检查点"], dependencies=_protected)
app.include_router(evaluation.router, prefix="/api/eval", tags=["评测"], dependencies=_protected)
app.include_router(scopes.router, prefix="/api/scopes", tags=["权限管理"], dependencies=_protected)


@app.on_event("startup")
async def startup_event():
    logger.info(f"Starting {settings.APP_NAME} (LangGraph版)...")

    # 初始化 PostgreSQL 连接池 + 评测表
    try:
        from app.core.database import get_pool
        await get_pool()
        logger.info("PostgreSQL 评测数据库初始化完成")
        # 幂等 seeding 预置角色
        from app.services.permission_service import ensure_builtin_roles
        await ensure_builtin_roles()
    except Exception as e:
        logger.warning(f"PostgreSQL 初始化失败（评测功能不可用）: {e}")

    # 恢复卡在 processing 状态的文档（服务器重启导致后台任务中断）
    try:
        from app.api.documents import METADATA_FILE, _load_metadata, _save_metadata
        import os
        if os.path.exists(METADATA_FILE):
            meta = _load_metadata()
            changed = False
            for doc_id, doc in meta.items():
                if doc.get("status") == "processing":
                    doc["status"] = "error"
                    doc["error"] = "处理中断（服务器重启），请重新上传"
                    changed = True
                    logger.info(f"恢复卡住的文档: {doc_id} ({doc.get('filename')})")
            if changed:
                _save_metadata(meta)
    except Exception as e:
        logger.warning(f"恢复文档状态失败: {e}")

    # 预加载Embedding模型到内存，避免首次请求冷启动
    try:
        from app.core.model_config import ModelManager
        from app.services.embedding_service import EmbeddingService
        model_manager = ModelManager.from_env()
        embedding_config = model_manager.get_embedding()
        if embedding_config:
            EmbeddingService(embedding_config)
            logger.info("Embedding模型预加载完成")
    except Exception as e:
        logger.warning(f"Embedding模型预加载失败（首次请求时会重新加载）: {e}")
    logger.info("Application started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Shutting down application...")
    logger.info("Application shutdown complete")


@app.get("/")
async def root():
    return {"message": "化工园区RAG知识库系统 API (LangGraph版)", "docs": "/docs", "health": "/health"}
