"""
迁移脚本：将已上传的文档重新索引到 Milvus（带 doc_id/status 字段）
用法：docker exec langgraph_rag_backend /app/.venv/bin/python /app/migrate_documents.py
"""
import json
import os
import sys
import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("migrate")

METADATA_PATH = "./uploads/metadata.json"


async def main():
    # 加载 metadata
    if not os.path.exists(METADATA_PATH):
        logger.error(f"metadata.json 不存在: {METADATA_PATH}")
        return

    with open(METADATA_PATH, "r", encoding="utf-8") as f:
        meta = json.load(f)

    # 只迁移 status=ready 的文档
    docs_to_migrate = [(did, doc) for did, doc in meta.items() if doc.get("status") == "ready"]
    logger.info(f"共找到 {len(docs_to_migrate)} 个需要迁移的文档")

    if not docs_to_migrate:
        logger.info("没有需要迁移的文档")
        return

    # 导入依赖
    sys.path.insert(0, "/app")
    from app.core.config import settings
    from app.vectorstore.hybrid_store import HybridMilvusStore
    from app.vectorstore.hnsw_index import HNSWConfig
    from app.vectorstore.bm25_index import BM25Index
    from app.services.embedding_service import EmbeddingService
    from app.core.model_config import ModelManager

    # 初始化 Milvus store
    hnsw_config = HNSWConfig.from_config(settings)
    bm25_index = BM25Index(index_path=getattr(settings, "BM25_INDEX_PATH", "./bm25_index"))
    model_manager = ModelManager.from_env()
    embedding_config = model_manager.get_embedding()
    embedding_service = EmbeddingService(embedding_config) if embedding_config else None

    store = HybridMilvusStore(
        host=settings.MILVUS_HOST, port=settings.MILVUS_PORT,
        collection_name=settings.MILVUS_COLLECTION_NAME,
        hnsw_config=hnsw_config, bm25_index=bm25_index,
        embedding_service=embedding_service,
    )
    await store.initialize()

    # 导入文档处理模块
    from app.api.documents import _parse_file, _index_chunks

    success_count = 0
    fail_count = 0

    for doc_id, doc in docs_to_migrate:
        filename = doc.get("filename", "")
        file_path = doc.get("file_path", "")
        file_ext = "." + doc.get("file_type", "")

        logger.info(f"迁移文档: {filename} (doc_id={doc_id})")

        if not file_path or not os.path.exists(file_path):
            logger.warning(f"  文件不存在，跳过: {file_path}")
            fail_count += 1
            continue

        try:
            # 解析文档
            chunks = await _parse_file(file_path, file_ext, original_filename=filename)
            if not chunks:
                logger.warning(f"  解析结果为空，跳过")
                fail_count += 1
                continue

            # 删除旧数据（如果有）
            try:
                await store.delete_by_doc_id(doc_id)
            except Exception:
                pass

            # 重新索引（带 doc_id）
            await _index_chunks(chunks, doc_id=doc_id)

            # 更新 metadata 中的 chunk_count
            meta[doc_id]["chunk_count"] = len(chunks)
            logger.info(f"  迁移成功: {len(chunks)} 个 chunk")
            success_count += 1

        except Exception as e:
            logger.error(f"  迁移失败: {e}")
            fail_count += 1

    # 保存更新后的 metadata
    with open(METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    logger.info(f"\n迁移完成: 成功 {success_count} 个, 失败 {fail_count} 个")


if __name__ == "__main__":
    asyncio.run(main())
