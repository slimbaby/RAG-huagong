"""
Reranker服务 - 交叉编码器精排
"""

from typing import List, Dict, Any, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

_model_instance = None

# 交叉编码器输入截断长度：512 token 足够覆盖 query+chunk 语义匹配，
# 不限制时 bge-reranker-v2-m3 最长支持 8192 token，长文档注意力计算量爆炸（实测 20 条耗时 60s+）
_MAX_LENGTH = 512
_BATCH_SIZE = 16


def _resolve_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _get_model(model_name: str):
    global _model_instance
    if _model_instance is None:
        try:
            from sentence_transformers import CrossEncoder
            device = _resolve_device()
            logger.info(f"正在加载Reranker模型: {model_name} (max_length={_MAX_LENGTH}, device={device})")
            _model_instance = CrossEncoder(model_name, max_length=_MAX_LENGTH, device=device)
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
            pairs = [(query, doc[:4000]) for doc in documents]
            scores = await asyncio.to_thread(
                self.model.predict, pairs,
                batch_size=_BATCH_SIZE, show_progress_bar=False,
            )
            return [float(s) for s in scores]
        except Exception as e:
            logger.error(f"Reranker打分失败: {e}")
            return [0.0] * len(documents)
