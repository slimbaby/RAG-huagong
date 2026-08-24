"""
Reranker服务 - 交叉编码器精排
"""

from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

_model_instance = None


def _get_model(model_name: str):
    global _model_instance
    if _model_instance is None:
        try:
            from sentence_transformers import CrossEncoder
            logger.info(f"正在加载Reranker模型: {model_name}")
            _model_instance = CrossEncoder(model_name)
            logger.info(f"Reranker模型加载完成: {model_name}")
        except Exception as e:
            logger.error(f"Reranker模型加载失败: {e}")
            _model_instance = None
    return _model_instance


class RerankerService:
    """交叉编码器 Reranker 服务"""

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        self.model_name = model_name
        self.model = _get_model(model_name)

    def is_available(self) -> bool:
        return self.model is not None

    async def rerank(self, query: str, documents: List[str]) -> List[float]:
        """对 query-document 对进行相关性打分"""
        if not self.is_available() or not documents:
            return [0.0] * len(documents)

        try:
            pairs = [(query, doc) for doc in documents]
            scores = self.model.predict(pairs)
            return [float(s) for s in scores]
        except Exception as e:
            logger.error(f"Reranker打分失败: {e}")
            return [0.0] * len(documents)
