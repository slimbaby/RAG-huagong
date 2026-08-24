"""
应用配置管理
"""

import os
from typing import List, Optional, Dict, Any
from pydantic_settings import BaseSettings
from pydantic import BaseModel, Field
from functools import lru_cache


class Settings(BaseSettings):
    """应用配置"""

    # 应用配置
    APP_NAME: str = "LangGraph RAG知识库系统"
    APP_ENV: str = "development"
    APP_DEBUG: bool = True
    APP_PORT: int = 8000
    APP_HOST: str = "0.0.0.0"

    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_MAX_BYTES: int = 10 * 1024 * 1024  # 10MB
    LOG_BACKUP_COUNT: int = 5

    # 记忆系统配置
    MEMORY_ENABLED: bool = True
    MEMORY_SHORT_TERM_MAX_MESSAGES: int = 60  # 短期记忆最大消息数（30轮对话）
    MEMORY_SHORT_TERM_TTL: int = 86400        # 短期记忆 TTL（秒），24小时
    MEMORY_LONG_TERM_SIMILARITY_THRESHOLD: float = 0.85  # 去重相似度阈值
    MEMORY_LONG_TERM_EXPIRE_DAYS: int = 30    # 长期记忆过期天数
    MEMORY_LONG_TERM_MIN_IMPORTANCE: float = 0.3  # 最低重要性阈值
    MEMORY_RECALL_TOP_K: int = 5              # 每次召回的记忆条数
    MEMORY_TOKEN_BUDGET: int = 1000           # 记忆注入的 Token 预算

    # CORS配置
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
    ]

    # Milvus配置
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    MILVUS_COLLECTION_NAME: str = "document_chunks"
    MILVUS_USER: str = ""
    MILVUS_PASSWORD: str = ""

    # RAGFlow配置
    RAGFLOW_ENABLED: bool = True
    RAGFLOW_HOST: str = "localhost"
    RAGFLOW_PORT: int = 9380
    RAGFLOW_API_KEY: str = ""
    RAGFLOW_DATASET_NAME: str = "chemical-rag-main"
    RAGFLOW_POLL_INTERVAL: float = 2.0
    RAGFLOW_POLL_TIMEOUT: float = 300.0
    RAGFLOW_FALLBACK_TO_LOCAL: bool = True

    # BM25配置
    BM25_K1: float = 1.5
    BM25_B: float = 0.75
    BM25_INDEX_PATH: str = "./bm25_index"

    # 混合检索配置
    HYBRID_VECTOR_WEIGHT: float = 0.6
    HYBRID_BM25_WEIGHT: float = 0.4
    HYBRID_FUSION_METHOD: str = "rrf"
    HYBRID_TOP_K: int = 5

    # HNSW配置
    HNSW_M: int = 32
    HNSW_EF_CONSTRUCTION: int = 256
    HNSW_EF_SEARCH: int = 128
    HNSW_METRIC_TYPE: str = "COSINE"

    # RAG配置
    CHUNK_SIZE: int = 1024
    CHUNK_OVERLAP: int = 100
    CHUNK_MIN_SIZE: int = 100
    TOP_K_RESULTS: int = 5

    # 表格切分配置
    TABLE_SEMANTIC_GROUPING: bool = True
    TABLE_MAX_CHUNK_CHARS: int = 2048
    TABLE_MAX_ROWS_PER_SUBGROUP: int = 20

    # 模型配置
    PREMIUM_MODEL_NAME: str = "mimo-72b"
    PREMIUM_MODEL_API_KEY: str = ""
    PREMIUM_MODEL_API_BASE_URL: str = "https://api.xiaomi.com/v1"
    PREMIUM_MODEL_TEMPERATURE: float = 0.7
    PREMIUM_MODEL_MAX_TOKENS: int = 4096

    EMBEDDING_MODEL_NAME: str = "BAAI/bge-base-zh-v1.5"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_API_BASE_URL: str = ""
    EMBEDDING_DIMENSION: int = 768

    # Reranker配置
    RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-v2-m3"
    RERANKER_ENABLED: bool = True
    RERANKER_TOP_K: int = 5

    # OCR配置
    OCR_ENGINE: str = "paddleocr"
    PADDLEOCR_LANG: str = "ch"
    TESSERACT_LANG: str = "chi_sim+eng"

    # 文件上传配置
    UPLOAD_DIR: str = "./uploads"
    MAX_FILE_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: List[str] = [
        ".pdf", ".docx", ".xlsx", ".xls", ".csv", ".json", ".txt", ".md"
    ]

    # PostgreSQL配置
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "your_password_here"
    POSTGRES_DB: str = "chemical_rag"

    # Redis配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: str = ""

    # 错误通知配置
    ERROR_WEBHOOK_URL: str = ""
    NOTIFICATION_EMAIL: str = "admin@example.com"

    # 法规爬取配置
    CRAWLER_ENABLED: bool = True
    CRAWLER_PROXY: str = ""
    CRAWLER_REQUEST_DELAY: float = 2.0
    CRAWLER_MAX_RETRIES: int = 3

    # 文件存储配置
    STORAGE_TYPE: str = "local"
    STORAGE_LOCAL_PATH: str = "./law_archives"

    # ERP配置（预留）
    ERP_API_BASE_URL: str = ""
    ERP_API_KEY: str = ""

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @property
    def DATABASE_URL(self) -> str:
        """PostgreSQL数据库连接URL"""
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def REDIS_URL(self) -> str:
        """Redis连接URL"""
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


# 全局配置实例
settings = get_settings()

# Redis 连接池（异步，评测任务状态用）
_redis = None


async def get_redis():
    """获取 Redis 异步连接（懒初始化）"""
    global _redis
    if _redis is None:
        import redis.asyncio as aioredis
        _redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis
