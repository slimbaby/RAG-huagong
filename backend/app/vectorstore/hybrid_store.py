"""
混合向量存储 - Milvus + BM25混合检索
"""

from typing import List, Dict, Optional
from dataclasses import dataclass
import logging
import asyncio

logger = logging.getLogger(__name__)


@dataclass
class HybridSearchResult:
    chunk_id: str
    content: str
    metadata: Dict
    vector_score: float = 0.0
    bm25_score: float = 0.0
    hybrid_score: float = 0.0
    rank: int = 0


class HybridMilvusStore:
    """混合向量存储 - Milvus HNSW + BM25"""

    def __init__(self, host: str, port: int, collection_name: str, hnsw_config, bm25_index, embedding_service):
        self.host = host
        self.port = port
        self.collection_name = collection_name
        self.hnsw_config = hnsw_config
        self.bm25 = bm25_index
        self.embeddings = embedding_service
        self.collection = None
        self._connected = False

    async def initialize(self):
        try:
            from pymilvus import connections, Collection, utility
            connections.connect(alias="default", host=self.host, port=self.port)
            self._connected = True

            if utility.has_collection(self.collection_name):
                self.collection = Collection(self.collection_name)
                # 检查是否需要 schema 迁移（旧 collection 缺少 doc_id/status 字段）
                schema_fields = {f.name for f in self.collection.schema.fields}
                if "doc_id" not in schema_fields or "status" not in schema_fields:
                    logger.info(f"检测到旧 schema，正在重建 collection: {self.collection_name}")
                    utility.drop_collection(self.collection_name)
                    await self._create_collection()
                else:
                    self.collection.load()
            else:
                await self._create_collection()
            logger.info(f"Milvus连接成功: {self.host}:{self.port}, collection: {self.collection_name}")
        except Exception as e:
            logger.error(f"Milvus初始化失败: {e}")
            self._connected = False

    async def _create_collection(self):
        from pymilvus import CollectionSchema, FieldSchema, Collection, DataType

        fields = [
            FieldSchema("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema("vector", DataType.FLOAT_VECTOR, dim=self.hnsw_config.metric_type if hasattr(self.hnsw_config, 'dimension') else 768),
            FieldSchema("content", DataType.VARCHAR, max_length=65535),
            FieldSchema("source_file", DataType.VARCHAR, max_length=512),
            FieldSchema("file_type", DataType.VARCHAR, max_length=32),
            FieldSchema("page_number", DataType.INT64),
            FieldSchema("line_start", DataType.INT64),
            FieldSchema("line_end", DataType.INT64),
            FieldSchema("content_type", DataType.VARCHAR, max_length=32),
            FieldSchema("section_title", DataType.VARCHAR, max_length=512),
            FieldSchema("chunk_index", DataType.INT64),
            FieldSchema("table_nl_description", DataType.VARCHAR, max_length=65535),
            FieldSchema("created_at", DataType.VARCHAR, max_length=64),
            FieldSchema("doc_id", DataType.VARCHAR, max_length=64, default_value=""),
            FieldSchema("status", DataType.VARCHAR, max_length=16, default_value="active"),
        ]

        schema = CollectionSchema(fields)
        self.collection = Collection(self.collection_name, schema)

        index_params = {
            "index_type": "HNSW",
            "metric_type": self.hnsw_config.metric_type,
            "params": self.hnsw_config.to_milvus_params(),
        }
        self.collection.create_index("vector", index_params)
        self.collection.create_index("source_file", {"index_type": "TRIE"})
        self.collection.create_index("content_type", {"index_type": "TRIE"})
        self.collection.create_index("doc_id", {"index_type": "TRIE"})
        self.collection.create_index("status", {"index_type": "TRIE"})
        self.collection.load()

    async def insert(self, chunks: List, doc_id: str = ""):
        if not self._connected or not self.collection:
            await self.initialize()
            if not self._connected or not self.collection:
                return

        def _get(c, key, default=None):
            if isinstance(c, dict):
                return c.get(key, default)
            return getattr(c, key, default)

        def _get_meta(c, key, default=None):
            if isinstance(c, dict):
                return c.get("metadata", {}).get(key, default)
            return getattr(c.metadata, key, default)

        data = [
            [_get(c, "chunk_id") for c in chunks],
            [_get(c, "vector") for c in chunks],
            [_get(c, "content", "")[:65535] for c in chunks],
            [_get_meta(c, "source_file", "unknown")[:512] for c in chunks],
            [_get_meta(c, "file_type", "unknown")[:32] for c in chunks],
            [_get_meta(c, "page_number", 0) or 0 for c in chunks],
            [_get_meta(c, "line_start", 0) or 0 for c in chunks],
            [_get_meta(c, "line_end", 0) or 0 for c in chunks],
            [_get_meta(c, "content_type", "text")[:32] for c in chunks],
            [(_get_meta(c, "section_title", "") or "")[:512] for c in chunks],
            [_get_meta(c, "chunk_index", 0) or 0 for c in chunks],
            [(_get_meta(c, "table_nl_description", "") or "")[:65535] for c in chunks],
            [str(_get_meta(c, "created_at", "")) for c in chunks],
            [doc_id[:64] for _ in chunks],
            ["active" for _ in chunks],
        ]

        self.collection.insert(data)
        self.collection.flush()

        for c in chunks:
            content = _get(c, "content", "")
            nl_desc = _get_meta(c, "table_nl_description", "")
            if nl_desc:
                content += " " + nl_desc
            self.bm25.add_document(_get(c, "chunk_id"), content, {"source_file": _get_meta(c, "source_file", "")})

    async def hybrid_search(self, query: str, query_vector: List[float], top_k: int = 5, vector_weight: float = 0.6, bm25_weight: float = 0.4, fusion_method: str = "rrf") -> List[HybridSearchResult]:
        if not self._connected or not self.collection:
            await self.initialize()
        logger.info(f"[hybrid_search] query='{query}', query_vector={'有' if query_vector else '无'}, connected={self._connected}, collection={'有' if self.collection else '无'}")
        vector_results, bm25_results = await asyncio.gather(
            self._vector_search(query_vector, top_k * 2),
            asyncio.to_thread(self._bm25_search, query, top_k * 2)
        )
        logger.info(f"[hybrid_search] vector_results={len(vector_results)}条, bm25_results={len(bm25_results)}条")

        if fusion_method == "rrf":
            return self._rrf_fusion(vector_results, bm25_results)[:top_k]
        elif fusion_method == "weighted":
            return self._weighted_fusion(vector_results, bm25_results, vector_weight, bm25_weight)[:top_k]
        else:
            return self._max_fusion(vector_results, bm25_results)[:top_k]

    async def _vector_search(self, query_vector: List[float], top_k: int) -> List[Dict]:
        if not self._connected or not self.collection:
            logger.warning("[_vector_search] 未连接或collection为空")
            return []
        if not query_vector:
            logger.warning("[_vector_search] query_vector为空")
            return []
        try:
            search_params = {"metric_type": self.hnsw_config.metric_type, "params": self.hnsw_config.to_search_params()}
            logger.info(f"[_vector_search] 开始搜索, top_k={top_k}, vector维度={len(query_vector)}")
            results = self.collection.search(
                data=[query_vector], anns_field="vector", param=search_params,
                limit=top_k, expr='status == "active"',
                output_fields=["chunk_id", "content", "source_file", "file_type", "page_number", "content_type", "section_title", "table_nl_description", "doc_id"]
            )
            search_results = []
            for hits in results:
                for hit in hits:
                    search_results.append({
                        "chunk_id": hit.entity.get("chunk_id"),
                        "content": hit.entity.get("content"),
                        "metadata": {
                            "source_file": hit.entity.get("source_file"),
                            "file_type": hit.entity.get("file_type"),
                            "page_number": hit.entity.get("page_number"),
                            "content_type": hit.entity.get("content_type"),
                            "section_title": hit.entity.get("section_title"),
                            "table_nl_description": hit.entity.get("table_nl_description"),
                        },
                        "vector_score": hit.score,
                    })
            return search_results
        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []

    def _bm25_search(self, query: str, top_k: int) -> List[Dict]:
        try:
            results = self.bm25.search(query, top_k)
            search_results = []
            for doc_id, score in results:
                doc_stats = self.bm25.doc_stats.get(doc_id, {})
                metadata = doc_stats.get("metadata", {})
                search_results.append({
                    "chunk_id": doc_id,
                    "content": "",
                    "metadata": metadata,
                    "bm25_score": score,
                })
            return search_results
        except Exception as e:
            logger.error(f"BM25检索失败: {e}")
            return []

    def _rrf_fusion(self, vector_results: List[Dict], bm25_results: List[Dict], k: int = 60) -> List[HybridSearchResult]:
        score_map = {}
        content_map = {}

        for rank, r in enumerate(vector_results):
            cid = r["chunk_id"]
            score_map[cid] = score_map.get(cid, 0) + 1 / (k + rank + 1)
            content_map[cid] = r

        for rank, r in enumerate(bm25_results):
            cid = r["chunk_id"]
            score_map[cid] = score_map.get(cid, 0) + 1 / (k + rank + 1)
            if cid not in content_map:
                content_map[cid] = r

        results = []
        for cid, score in sorted(score_map.items(), key=lambda x: x[1], reverse=True):
            r = content_map[cid]
            results.append(HybridSearchResult(
                chunk_id=cid, content=r.get("content", ""), metadata=r.get("metadata", {}),
                vector_score=r.get("vector_score", 0), bm25_score=r.get("bm25_score", 0),
                hybrid_score=score,
            ))

        for i, r in enumerate(results):
            r.rank = i + 1
        return results

    def _weighted_fusion(self, vector_results: List[Dict], bm25_results: List[Dict], vector_weight: float, bm25_weight: float) -> List[HybridSearchResult]:
        score_map = {}
        content_map = {}

        max_vec = max((r.get("vector_score", 0) for r in vector_results), default=1) or 1
        for r in vector_results:
            cid = r["chunk_id"]
            score_map[cid] = score_map.get(cid, {})
            score_map[cid]["vector"] = r.get("vector_score", 0) / max_vec
            content_map[cid] = r

        max_bm25 = max((r.get("bm25_score", 0) for r in bm25_results), default=1) or 1
        for r in bm25_results:
            cid = r["chunk_id"]
            score_map[cid] = score_map.get(cid, {})
            score_map[cid]["bm25"] = r.get("bm25_score", 0) / max_bm25
            if cid not in content_map:
                content_map[cid] = r

        results = []
        for cid, scores in score_map.items():
            hybrid = scores.get("vector", 0) * vector_weight + scores.get("bm25", 0) * bm25_weight
            r = content_map[cid]
            results.append(HybridSearchResult(
                chunk_id=cid, content=r.get("content", ""), metadata=r.get("metadata", {}),
                vector_score=r.get("vector_score", 0), bm25_score=r.get("bm25_score", 0),
                hybrid_score=hybrid,
            ))

        results.sort(key=lambda x: x.hybrid_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1
        return results

    def _max_fusion(self, vector_results: List[Dict], bm25_results: List[Dict]) -> List[HybridSearchResult]:
        score_map = {}
        content_map = {}

        max_vec = max((r.get("vector_score", 0) for r in vector_results), default=1) or 1
        for r in vector_results:
            cid = r["chunk_id"]
            score_map[cid] = score_map.get(cid, {})
            score_map[cid]["vector"] = r.get("vector_score", 0) / max_vec
            content_map[cid] = r

        max_bm25 = max((r.get("bm25_score", 0) for r in bm25_results), default=1) or 1
        for r in bm25_results:
            cid = r["chunk_id"]
            score_map[cid] = score_map.get(cid, {})
            score_map[cid]["bm25"] = r.get("bm25_score", 0) / max_bm25
            if cid not in content_map:
                content_map[cid] = r

        results = []
        for cid, scores in score_map.items():
            hybrid = max(scores.get("vector", 0), scores.get("bm25", 0))
            r = content_map[cid]
            results.append(HybridSearchResult(
                chunk_id=cid, content=r.get("content", ""), metadata=r.get("metadata", {}),
                vector_score=r.get("vector_score", 0), bm25_score=r.get("bm25_score", 0),
                hybrid_score=hybrid,
            ))

        results.sort(key=lambda x: x.hybrid_score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1
        return results

    async def delete_by_file(self, source_file: str):
        if not self._connected or not self.collection:
            return
        try:
            self.collection.delete(f'source_file == "{source_file}"')
            self._rebuild_bm25_index()
        except Exception as e:
            logger.error(f"删除文档失败: {e}")

    async def delete_by_doc_id(self, doc_id: str):
        """按 doc_id 物理删除所有关联 chunk"""
        if not self._connected or not self.collection:
            await self.initialize()
            if not self._connected or not self.collection:
                return
        try:
            self.collection.delete(f'doc_id == "{doc_id}"')
            self.collection.flush()
            self._rebuild_bm25_index()
            logger.info(f"已删除 doc_id={doc_id} 的所有 chunk")
        except Exception as e:
            logger.error(f"按doc_id删除文档失败: {e}")

    def _rebuild_bm25_index(self):
        if not self._connected or not self.collection:
            return
        try:
            results = self.collection.query(expr="", output_fields=["chunk_id", "content", "source_file"], limit=10000)
            self.bm25 = type(self.bm25)(k1=self.bm25.k1, b=self.bm25.b, index_path=self.bm25.index_path)
            for r in results:
                self.bm25.add_document(r["chunk_id"], r["content"], {"source_file": r["source_file"]})
        except Exception as e:
            logger.error(f"重建BM25索引失败: {e}")

    def get_stats(self) -> Dict:
        stats = {"milvus_connected": self._connected, "bm25": self.bm25.get_stats()}
        if self._connected and self.collection:
            stats["entity_count"] = self.collection.num_entities
        return stats
