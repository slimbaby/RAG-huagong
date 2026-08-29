"""
认证与密码安全工具 - JWT 签发/校验 + passlib 密码哈希
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
import logging
import secrets

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError
import bcrypt

logger = logging.getLogger(__name__)

bearer_scheme = HTTPBearer(auto_error=False)

_ephemeral_secret: Optional[str] = None

# passlib 1.7.4 与 bcrypt>=4.1 不兼容（backend 探测时直接 ValueError），故直接用 bcrypt。
# bcrypt 密钥上限 72 字节，超出部分截断（业界标准做法）。
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8")[:_BCRYPT_MAX_BYTES],
            password_hash.encode("utf-8"),
        )
    except Exception:
        return False


def get_jwt_secret() -> str:
    """JWT 签名密钥：优先用配置，未配置则进程内随机生成一次（重启后 token 失效）"""
    global _ephemeral_secret
    from .config import settings
    if settings.JWT_SECRET_KEY:
        return settings.JWT_SECRET_KEY
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(32)
        logger.warning("JWT_SECRET_KEY 未配置，已生成临时随机密钥（服务重启后所有 token 失效）")
    return _ephemeral_secret


def create_access_token(username: str) -> str:
    from .config import settings
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": username, "exp": expire}
    return jwt.encode(payload, get_jwt_secret(), algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    """解码 token 返回用户名，无效/过期返回 None"""
    from .config import settings
    try:
        payload = jwt.decode(token, get_jwt_secret(), algorithms=[settings.JWT_ALGORITHM])
        username = payload.get("sub")
        return username if isinstance(username, str) and username else None
    except JWTError:
        return None
    except Exception:
        return None


async def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> str:
    """路由级认证依赖：校验 Authorization: Bearer <JWT>，返回用户名。
    AUTH_ENABLED=False 时直接放行（开发模式）。"""
    from .config import settings
    if not settings.AUTH_ENABLED:
        return "anonymous"
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="未提供认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    username = decode_access_token(credentials.credentials)
    if username is None:
        raise HTTPException(
            status_code=401,
            detail="token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username
