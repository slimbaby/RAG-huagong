"""
混合向量存储 - Milvus + BM25混合检索
"""

from typing import List, Dict, Optional, Set
from dataclasses import dataclass
import logging
import asyncio

from ..resilience.executor import get_executor

logger = logging.getLogger(__name__)


def _milvus_escape(value: str) -> str:
    """转义 Milvus 布尔表达式中的字符串字面量，防止文件名/doc_id 中的
    引号和反斜杠破坏表达式结构（表达式注入）。
    返回带双引号的字符串，可直接用于 Milvus 表达式"""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_eq_expr(field: str, value: str) -> str:
    """构造 field == "value" 形式的 Milvus 等值表达式。

    _milvus_escape 返回值已含引号，调用点不得再外包一层引号——
    此前 delete_by_file/delete_by_doc_id/query_by_file 三处曾写成
    f'f == "{_milvus_escape(v)}"'，生成 f == ""v"" 非法表达式，
    且异常被 except 吞掉导致删除静默失败。统一走本函数防止回归。"""
    return f"{field} == {_milvus_escape(value)}"


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
                    logger.error(
                        f"检测到旧 schema，正在删除并重建 collection: {self.collection_name}。"
                        f"注意：此操作将丢弃该集合中的全部向量数据！如需保留请先导出/重传文档。"
                    )
                    utility.drop_collection(self.collection_name)
                    await self._create_collection()
                else:
                    self.collection.load()
            else:
                logger.warning(
                    f"collection '{self.collection_name}' 不存在，将创建空集合。"
                    f"若此前已有知识库数据，说明 Milvus 元数据丢失（容器重建等），需要重新上传文档！"
                )
                await self._create_collection()
            logger.info(f"Milvus连接成功: {self.host}:{self.port}, collection: {self.collection_name}")
            await self._check_kb_consistency()
        except Exception as e:
            logger.error(f"Milvus初始化失败: {e}")
            self._connected = False

    async def _check_kb_consistency(self):
        """一致性自检：向量库为空但 BM25 索引仍有数据时大声报警，
        避免系统静默降级成'无正文的BM25模式'导致检索质量劣化无人察觉。"""
        try:
            entity_count = self.collection.num_entities if self.collection else 0
            bm25_docs = getattr(self.bm25, "doc_count", 0)
            if entity_count == 0 and bm25_docs > 0:
                logger.error(
                    f"[知识库不一致] 向量库 '{self.collection_name}' 为空，但 BM25 索引仍有 "
                    f"{bm25_docs} 条数据。向量数据很可能已因 Milvus 重建/元数据丢失而损坏，"
                    f"当前仅剩 BM25 兜底检索，召回质量会显著下降。请尽快重新上传文档以重建向量库！"
                )
        except Exception as e:
            logger.warning(f"知识库一致性检查失败（不影响服务）: {e}")

    async def _create_collection(self):
        from pymilvus import CollectionSchema, FieldSchema, Collection, DataType

        fields = [
            FieldSchema("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64),
            FieldSchema("vector", DataType.FLOAT_VECTOR, dim=self.hnsw_config.dimension),
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

        def _do_insert():
            self.collection.insert(data)
            self.collection.flush()

        await asyncio.to_thread(_do_insert)

        def _do_bm25():
            for c in chunks:
                content = _get(c, "content", "")
                nl_desc = _get_meta(c, "table_nl_description", "")
                if nl_desc:
                    content += " " + nl_desc
                self.bm25.add_document(_get(c, "chunk_id"), content, {"source_file": _get_meta(c, "source_file", "")})
            self._save_bm25()

        await asyncio.to_thread(_do_bm25)

    async def hybrid_search(
        self,
        query: str,
        query_vector: List[float],
        top_k: int = 5,
        vector_weight: float = 0.6,
        bm25_weight: float = 0.4,
        fusion_method: str = "rrf",
        allowed_doc_ids: "Optional[set]" = None,
    ) -> "List[HybridSearchResult]":
        """
        混合检索。allowed_doc_ids=None 表示不过滤（admin）；
        传空 set 表示没有任何可见文档，直接返回 []。
        """
        if not self._connected or not self.collection:
            await self.initialize()
        logger.info(f"[hybrid_search] query='{query}', allowed_doc_ids={'all' if allowed_doc_ids is None else len(allowed_doc_ids)}")

        # scope 过滤：预先查询可见 chunk_ids，供 BM25 post-filter 使用
        allowed_chunk_ids: "Optional[set]" = None
        if allowed_doc_ids is not None:
            if not allowed_doc_ids:
                return []
            allowed_chunk_ids = await asyncio.to_thread(self._get_chunk_ids_for_docs, allowed_doc_ids)
            if not allowed_chunk_ids:
                logger.info("[hybrid_search] allowed_doc_ids 非空但对应 chunk 为空，返回 0 结果")
                return []

        vector_results, bm25_results = await asyncio.gather(
            asyncio.to_thread(self._vector_search, query_vector, top_k * 2, allowed_doc_ids),
            asyncio.to_thread(self._bm25_search, query, top_k * 2, allowed_chunk_ids)
        )
        logger.info(f"[hybrid_search] vector_results={len(vector_results)}条, bm25_results={len(bm25_results)}条")

        if fusion_method == "rrf":
            return self._rrf_fusion(vector_results, bm25_results)[:top_k]
        elif fusion_method == "weighted":
            return self._weighted_fusion(vector_results, bm25_results, vector_weight, bm25_weight)[:top_k]
        else:
            return self._max_fusion(vector_results, bm25_results)[:top_k]

    def _get_chunk_ids_for_docs(self, allowed_doc_ids: set) -> set:
        """查询 Milvus 中指定 doc_id 集合对应的所有 chunk_id（用于 BM25 post-filter）
        使用分页查询避免超过 Milvus 的 16384 限制
        """
        if not allowed_doc_ids or not self._connected or not self.collection:
            return set()
        try:
            escaped = [_milvus_escape(d) for d in allowed_doc_ids]
            expr = f'doc_id in [{", ".join(escaped)}]'
            # 分页查询，每页最多 10000 条
            all_chunk_ids = set()
            offset = 0
            page_size = 10000
            while True:
                results = self.collection.query(
                    expr=expr,
                    output_fields=["chunk_id"],
                    limit=page_size,
                    offset=offset,
                )
                if not results:
                    break
                all_chunk_ids.update(r["chunk_id"] for r in results)
                if len(results) < page_size:
                    break  # 最后一页
                offset += page_size
                # 防止无限循环（最多查 100 万条）
                if offset >= 1000000:
                    logger.warning(f"查询 chunk_ids 超过 100 万条，提前终止")
                    break
            return all_chunk_ids
        except Exception as e:
            logger.warning(f"查询 scope chunk_ids 失败: {e}")
            return set()

    def _vector_search(self, query_vector: List[float], top_k: int, allowed_doc_ids: "Optional[set]" = None) -> List[Dict]:
        if not self._connected or not self.collection:
            logger.warning("[_vector_search] 未连接或collection为空")
            return []
        if not query_vector:
            logger.warning("[_vector_search] query_vector为空")
            return []
        try:
            search_params = {"metric_type": self.hnsw_config.metric_type, "params": self.hnsw_config.to_search_params()}
            expr = 'status == "active"'
            if allowed_doc_ids is not None:
                if not allowed_doc_ids:
                    return []
                escaped = [_milvus_escape(d) for d in allowed_doc_ids]
                expr += f' AND doc_id in [{", ".join(escaped)}]'
            logger.info(f"[_vector_search] 开始搜索, top_k={top_k}, vector维度={len(query_vector)}, expr={expr}")
            # 韧性执行器包裹：Milvus 连续故障时熔断快速失败（下方 except 接住
            # CircuitBreakerError 返回 []，检索自动退化为 BM25 单路，不级联拖死请求）。
            # 同步调用无法安全中断线程，故只熔断不加超时、不重试
            results = get_executor().execute_sync(
                "milvus",
                lambda: self.collection.search(
                    data=[query_vector], anns_field="vector", param=search_params,
                    limit=top_k, expr=expr,
                    output_fields=["chunk_id", "content", "source_file", "file_type", "page_number", "content_type", "section_title", "table_nl_description", "doc_id"]
                ),
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
                            "doc_id": hit.entity.get("doc_id"),
                        },
                        "vector_score": hit.score,
                    })
            return search_results
        except Exception as e:
            logger.error(f"向量检索失败: {e}")
            return []

    def _bm25_search(self, query: str, top_k: int, allowed_chunk_ids: "Optional[set]" = None) -> List[Dict]:
        try:
            # 韧性执行器包裹（同 _vector_search：仅熔断；BM25 是纯内存计算，
            # 熔断主要防索引损坏等持续性故障时的反复异常开销）
            results = get_executor().execute_sync(
                "bm25",
                lambda: self.bm25.search(query, top_k * 2 if allowed_chunk_ids else top_k),
            )
            if allowed_chunk_ids is not None:
                # post-filter：只保留 scope 内的 chunk
                results = [(cid, score) for cid, score in results if cid in allowed_chunk_ids]
                results = results[:top_k]
            search_results = []
            for doc_id, score in results:
                stats = self.bm25.doc_stats.get(doc_id, {})
                metadata = stats.get("metadata", {})
                search_results.append({
                    "chunk_id": doc_id,
                    # 回填正文：向量侧失效时 BM25 命中仍可参与 rerank/实体兜底/引用，
                    # 否则空 content 会被 reranker 打出近零分导致整条链路误判
                    "content": stats.get("content", ""),
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

    def _save_bm25(self):
        try:
            self.bm25.save()
        except Exception as e:
            logger.warning(f"BM25索引持久化失败: {e}")

    async def delete_by_file(self, source_file: str):
        if not self._connected or not self.collection:
            return
        try:
            expr = _build_eq_expr("source_file", source_file)

            def _do():
                self.collection.delete(expr)
                self.collection.flush()

            await asyncio.to_thread(_do)
            await asyncio.to_thread(self._rebuild_bm25_index)
        except Exception as e:
            logger.error(f"删除文档失败: {e}")
            # 删除失败必须让调用方感知：吞掉异常会造成孤儿 chunk 残留、
            # 检索结果重复，且 documents.py「删除失败中止重索引」的保护失效
            raise

    async def delete_by_doc_id(self, doc_id: str):
        """按 doc_id 物理删除所有关联 chunk"""
        if not self._connected or not self.collection:
            await self.initialize()
            if not self._connected or not self.collection:
                return
        try:
            expr = _build_eq_expr("doc_id", doc_id)

            def _do():
                self.collection.delete(expr)
                self.collection.flush()

            await asyncio.to_thread(_do)
            await asyncio.to_thread(self._rebuild_bm25_index)
            logger.info(f"已删除 doc_id={doc_id} 的所有 chunk")
        except Exception as e:
            logger.error(f"按doc_id删除文档失败: {e}")
            raise

    async def query_by_file(self, source_file: str) -> List[Dict]:
        """按源文件名查询全部 chunk（按 chunk_index 排序）"""
        if not source_file:
            return []
        if not self._connected or not self.collection:
            await self.initialize()
            if not self._connected or not self.collection:
                return []

        expr = _build_eq_expr("source_file", source_file)

        def _do():
            return self.collection.query(
                expr=expr,
                output_fields=["chunk_id", "content", "source_file", "file_type", "page_number",
                               "line_start", "line_end", "content_type", "section_title",
                               "table_nl_description", "chunk_index", "created_at"],
            )

        try:
            rows = await asyncio.to_thread(_do) or []
        except Exception as e:
            logger.error(f"按文件查询chunks失败: {e}")
            return []
        rows.sort(key=lambda r: r.get("chunk_index", 0))
        return rows

    def _rebuild_bm25_index(self):
        if not self._connected or not self.collection:
            return
        try:
            self.bm25 = type(self.bm25)(k1=self.bm25.k1, b=self.bm25.b, index_path=self.bm25.index_path)
            total = 0
            # 用 query_iterator 分批拉取全量数据：单次 query(limit=10000) 超量会静默截断，
            # 导致 BM25 与 Milvus 永久不一致
            try:
                iterator = self.collection.query_iterator(
                    batch_size=1000, expr="",
                    output_fields=["chunk_id", "content", "source_file"],
                )
                while True:
                    batch = iterator.next()
                    if not batch:
                        break
                    for r in batch:
                        self.bm25.add_document(r["chunk_id"], r["content"], {"source_file": r["source_file"]})
                    total += len(batch)
                iterator.close()
            except (AttributeError, TypeError):
                # 旧版 pymilvus 无 query_iterator，退化为 offset 分页
                page_size, offset = 1000, 0
                while True:
                    rows = self.collection.query(
                        expr="", output_fields=["chunk_id", "content", "source_file"],
                        limit=page_size, offset=offset,
                    )
                    if not rows:
                        break
                    for r in rows:
                        self.bm25.add_document(r["chunk_id"], r["content"], {"source_file": r["source_file"]})
                    total += len(rows)
                    offset += page_size
                    if len(rows) < page_size:
                        break
            logger.info(f"BM25索引重建完成，共 {total} 条")
            self._save_bm25()
        except Exception as e:
            logger.error(f"重建BM25索引失败: {e}")

    def get_stats(self) -> Dict:
        stats = {"milvus_connected": self._connected, "bm25": self.bm25.get_stats()}
        if self._connected and self.collection:
            stats["entity_count"] = self.collection.num_entities
        return stats
