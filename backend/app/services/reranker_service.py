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
_QUERY_TOKEN_RESERVE = 64  # 给 query 与特殊 token 预留，避免 doc 挤占后尾部被静默截断
_DOC_CHAR_BUDGET = 1200   # tokenizer 不可用时的字符兜底窗口
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

    def _split_doc(self, doc: str) -> List[str]:
        """按 reranker 的 token 预算切窗，超长文档分窗打分取最高分。

        原实现喂 4000 字符但模型只保留前 512 token，答案落在 chunk 尾部时分数被
        系统性低估、精排后被截断漏排。这里按 tokenizer 真实预算切窗。
        """
        tokenizer = getattr(_model_instance, "tokenizer", None) if _model_instance else None
        budget = max(64, _MAX_LENGTH - _QUERY_TOKEN_RESERVE)
        if tokenizer is not None:
            try:
                token_ids = tokenizer.encode(doc, add_special_tokens=False)
                if len(token_ids) <= budget:
                    return [doc]
                return [
                    tokenizer.decode(token_ids[i:i + budget], skip_special_tokens=True)
                    for i in range(0, len(token_ids), budget)
                ]
            except Exception as e:
                logger.warning(f"Reranker 分窗失败，回退字符切分: {e}")
        return [doc[i:i + _DOC_CHAR_BUDGET] for i in range(0, len(doc), _DOC_CHAR_BUDGET)] or [""]

    async def rerank(self, query: str, documents: List[str]) -> List[float]:
        """对 query-document 对进行相关性打分（超长文档分窗取最高分）"""
        if not self.is_available() or not documents:
            return [0.0] * len(documents)

        try:
            window_counts = []
            pairs = []
            for doc in documents:
                windows = self._split_doc(doc or "")
                window_counts.append(len(windows))
                pairs.extend((query, window) for window in windows)
            scores = await asyncio.to_thread(
                self.model.predict, pairs,
                batch_size=_BATCH_SIZE, show_progress_bar=False,
            )
            result = []
            pos = 0
            for count in window_counts:
                result.append(max(float(s) for s in scores[pos:pos + count]))
                pos += count
            return result
        except Exception as e:
            logger.error(f"Reranker打分失败: {e}")
            return [0.0] * len(documents)
