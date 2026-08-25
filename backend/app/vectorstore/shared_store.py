"""
共享混合检索存储 - 全局唯一 HybridMilvusStore 实例

所有读写路径（chat/documents/evaluation）共用同一实例，
避免重复建立 Milvus 连接，并保证 BM25 内存态与磁盘态一致。
"""

import logging

logger = logging.getLogger(__name__)

_shared_store = None


def get_shared_store():
    """获取全局唯一的混合检索存储实例（BM25 索引从磁盘加载）"""
    global _shared_store
    if _shared_store is None:
        from ..core.config import settings
        from .hybrid_store import HybridMilvusStore
        from .hnsw_index import HNSWConfig
        from .bm25_index import BM25Index

        hnsw_config = HNSWConfig.from_config(settings)
        index_path = getattr(settings, "BM25_INDEX_PATH", "./bm25_index")
        bm25_index = BM25Index.load(index_path)
        stats = bm25_index.get_stats()
        if stats["doc_count"] > 0:
            logger.info(
                f"BM25索引已从磁盘加载: {stats['doc_count']} 条, "
                f"词表 {stats['vocabulary_size']} 项 ({index_path}.pickle)"
            )
        _shared_store = HybridMilvusStore(
            host=settings.MILVUS_HOST,
            port=settings.MILVUS_PORT,
            collection_name=settings.MILVUS_COLLECTION_NAME,
            hnsw_config=hnsw_config,
            bm25_index=bm25_index,
            embedding_service=None,
        )
    return _shared_store
