"""
模型配置管理 - 统一管理不同AI模型的配置
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum


class ModelProvider(str, Enum):
    """模型提供商"""
    XIAOMI = "xiaomi"
    OPENAI = "openai"
    LOCAL = "local"


class ModelLevel(str, Enum):
    """模型级别"""
    PREMIUM = "premium"
    STANDARD = "standard"
    LIGHTWEIGHT = "lightweight"


class ModelConfig(BaseModel):
    """单个模型配置"""
    provider: ModelProvider
    model_name: str
    api_key: str
    api_base_url: str
    temperature: float = 0.7
    max_tokens: int = 4096
    max_concurrent: int = 10
    timeout: int = 30
    supports_vision: bool = False
    supports_streaming: bool = True

    class Config:
        use_enum_values = True


class EmbeddingConfig(BaseModel):
    """Embedding模型配置"""
    provider: ModelProvider
    model_name: str
    api_key: str
    api_base_url: str
    dimension: int = 2048
    batch_size: int = 32
    max_retries: int = 3

    class Config:
        use_enum_values = True


class ModelManager:
    """模型管理器 - 统一管理所有模型配置"""

    def __init__(self):
        self._models: Dict[str, ModelConfig] = {}
        self._embeddings: Dict[str, EmbeddingConfig] = {}

    def register_model(self, level: ModelLevel, config: ModelConfig):
        """注册模型配置"""
        self._models[level.value] = config

    def register_embedding(self, name: str, config: EmbeddingConfig):
        """注册Embedding配置"""
        self._embeddings[name] = config

    def get_model(self, level: ModelLevel) -> Optional[ModelConfig]:
        """获取指定级别的模型配置"""
        return self._models.get(level.value)

    def get_embedding(self, name: str = "default") -> Optional[EmbeddingConfig]:
        """获取Embedding配置"""
        return self._embeddings.get(name)

    def get_best_available_model(
        self,
        required_level: ModelLevel = ModelLevel.PREMIUM,
        supports_vision: bool = False
    ) -> Optional[ModelConfig]:
        """获取最优可用模型"""
        levels = [ModelLevel.PREMIUM, ModelLevel.STANDARD, ModelLevel.LIGHTWEIGHT]

        for level in levels:
            if level.value < required_level.value:
                continue

            config = self._models.get(level.value)
            if config:
                if supports_vision and not config.supports_vision:
                    continue
                return config

        return None

    @classmethod
    def from_env(cls) -> "ModelManager":
        """从环境变量创建模型管理器"""
        from .config import settings

        manager = cls()

        # 注册高阶模型
        premium_config = ModelConfig(
            provider=ModelProvider.XIAOMI,
            model_name=getattr(settings, "PREMIUM_MODEL_NAME", "mimo-72b-vision"),
            api_key=getattr(settings, "PREMIUM_MODEL_API_KEY", ""),
            api_base_url=getattr(settings, "PREMIUM_MODEL_API_BASE_URL", "https://api.xiaomi.com/v1"),
            temperature=getattr(settings, "PREMIUM_MODEL_TEMPERATURE", 0.7),
            max_tokens=getattr(settings, "PREMIUM_MODEL_MAX_TOKENS", 4096),
            max_concurrent=getattr(settings, "PREMIUM_MODEL_MAX_CONCURRENT", 10),
            supports_vision=True,
        )
        manager.register_model(ModelLevel.PREMIUM, premium_config)

        # 注册标准模型
        standard_config = ModelConfig(
            provider=ModelProvider.XIAOMI,
            model_name=getattr(settings, "STANDARD_MODEL_NAME", "mimo-7b-chat"),
            api_key=getattr(settings, "STANDARD_MODEL_API_KEY", ""),
            api_base_url=getattr(settings, "STANDARD_MODEL_API_BASE_URL", "https://api.xiaomi.com/v1"),
            temperature=getattr(settings, "STANDARD_MODEL_TEMPERATURE", 0.7),
            max_tokens=getattr(settings, "STANDARD_MODEL_MAX_TOKENS", 2048),
            max_concurrent=getattr(settings, "STANDARD_MODEL_MAX_CONCURRENT", 50),
            supports_vision=False,
        )
        manager.register_model(ModelLevel.STANDARD, standard_config)

        # 注册Embedding
        embedding_config = EmbeddingConfig(
            provider=ModelProvider.LOCAL,
            model_name=getattr(settings, "EMBEDDING_MODEL_NAME", "BAAI/bge-base-zh-v1.5"),
            api_key=getattr(settings, "EMBEDDING_API_KEY", ""),
            api_base_url=getattr(settings, "EMBEDDING_API_BASE_URL", ""),
            dimension=getattr(settings, "EMBEDDING_DIMENSION", 768),
        )
        manager.register_embedding("default", embedding_config)

        return manager


# 全局模型管理器
model_manager = ModelManager.from_env()
