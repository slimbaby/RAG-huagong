"""
健康检查API
"""

from fastapi import APIRouter
from ..core.config import settings

router = APIRouter()


@router.get("/health", summary="健康检查")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": "1.0.0",
        "environment": settings.APP_ENV
    }


@router.get("/health/detailed", summary="详细健康检查")
async def detailed_health_check():
    return {
        "status": "healthy",
        "components": {
            "api": "healthy",
            "milvus": "unknown",
            "redis": "unknown",
            "llm": "unknown"
        }
    }
