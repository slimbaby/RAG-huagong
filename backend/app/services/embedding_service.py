"""
Embedding服务 - 本地BGE模型
"""

from typing import List
import asyncio
import logging
import numpy as np

from ..core.model_config import EmbeddingConfig

logger = logging.getLogger(__name__)

_model_instance = None


def _get_model(model_name: str):
    global _model_instance
    if _model_instance is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"正在加载本地Embedding模型: {model_name}")
        _model_instance = SentenceTransformer(model_name)
        logger.info(f"模型加载完成: {model_name}, 向量维度: {_model_instance.get_sentence_embedding_dimension()}")
    return _model_instance


class EmbeddingService:
    """本地BGE Embedding服务"""

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.model = _get_model(config.model_name)

    async def embed_query(self, text: str) -> List[float]:
        try:
            query_text = f"为这个句子生成表示以用于检索相关文章：{text}"
            embedding = await asyncio.to_thread(
                self.model.encode, query_text, normalize_embeddings=True
            )
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Embedding查询失败: {e}")
            raise

    async def embed_documents(self, texts: List[str]) -> List[List[float]]:
        try:
            embeddings = await asyncio.to_thread(
                self.model.encode, texts,
                normalize_embeddings=True,
                batch_size=self.config.batch_size,
                show_progress_bar=False,
            )
            return embeddings.tolist()
        except Exception as e:
            logger.error(f"Embedding批量查询失败: {e}")
            raise
