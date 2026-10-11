"""
文档管理API
"""

from fastapi import APIRouter, UploadFile, File, HTTPException, Query, BackgroundTasks, Form, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
import os
import re
import uuid
import json
import hashlib
import logging
import asyncio

from .auth import CurrentUser, get_current_user
from ..services import permission_service

logger = logging.getLogger(__name__)
router = APIRouter()

METADATA_FILE = "./uploads/metadata.json"
CHECKPOINT_DIR = "./uploads/checkpoints"


def _sanitize_filename(filename: str) -> str:
    """清洗文件名：剥离路径部分和危险字符，防止路径遍历（如 ..\\..\\evil.pdf）。"""
    name = os.path.basename(filename.replace("\\", "/"))
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip(". ")
    return name or "unnamed"


def _safe_upload_path(upload_dir: str, doc_id: str, filename: str) -> str:
    """在 upload_dir 内生成安全的文件保存路径，并校验最终路径未逃逸出上传目录。"""
    file_path = os.path.abspath(os.path.join(upload_dir, f"{doc_id}_{_sanitize_filename(filename)}"))
    upload_dir_abs = os.path.abspath(upload_dir)
    if not file_path.startswith(upload_dir_abs + os.sep):
        raise HTTPException(status_code=400, detail="非法的文件名")
    return file_path


def _compute_file_hash(content: bytes) -> str:
    return hashlib.md5(content).hexdigest()[:12]


def _get_checkpoint_path(file_hash: str) -> str:
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    return os.path.join(CHECKPOINT_DIR, f"{file_hash}.json")


def _load_metadata() -> dict:
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    if not os.path.exists(METADATA_FILE):
        return _scan_existing_files()
    try:
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return _scan_existing_files()
        return data
    except Exception:
        return _scan_existing_files()


def _scan_existing_files() -> dict:
    upload_dir = os.path.dirname(METADATA_FILE)
    if not os.path.exists(upload_dir):
        return {}
    meta = {}
    for fname in os.listdir(upload_dir):
        if fname == "metadata.json":
            continue
        fpath = os.path.join(upload_dir, fname)
        if not os.path.isfile(fpath):
            continue
        if "_" in fname:
            doc_id = fname.split("_", 1)[0]
            original_name = fname.split("_", 1)[1]
        else:
            doc_id = fname[:8]
            original_name = fname
        file_ext = os.path.splitext(original_name)[1].lstrip(".").lower()
        file_size = os.path.getsize(fpath)
        mtime = datetime.fromtimestamp(os.path.getmtime(fpath))
        meta[doc_id] = {
            "doc_id": doc_id, "filename": original_name, "file_type": file_ext,
            "file_size": file_size, "chunk_count": 0, "upload_time": mtime.isoformat(),
            "status": "ready", "file_path": fpath,
        }
    _save_metadata(meta)
    return meta


def _save_metadata(data: dict):
    os.makedirs(os.path.dirname(METADATA_FILE), exist_ok=True)
    with open(METADATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class DocumentInfo(BaseModel):
    doc_id: str
    filename: str
    file_type: str
    file_size: int
    chunk_count: int
    upload_time: str
    status: str
    metadata: Optional[dict] = None
    scope_ids: List[str] = []


class DocumentListResponse(BaseModel):
    total: int
    documents: List[DocumentInfo]


async def _enrich_with_scopes(docs: List[dict]) -> List[dict]:
    """给文档列表批量填充 scope_ids（从 doc_scopes 表）"""
    if not docs:
        return docs
    doc_ids = [d["doc_id"] for d in docs]
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT doc_id, scope_id FROM doc_scopes WHERE doc_id = ANY($1)",
            doc_ids,
        )
    scope_map: dict = {}
    for r in rows:
        scope_map.setdefault(r["doc_id"], []).append(r["scope_id"])
    for d in docs:
        d["scope_ids"] = scope_map.get(d["doc_id"], [])
    return docs


async def _check_doc_access(user: CurrentUser, doc_id: str):
    """校验当前用户对文档的访问权限，无权限抛 403"""
    if not await permission_service.user_can_access_doc(user.username, doc_id):
        raise HTTPException(status_code=403, detail="无权访问此文档")


async def _process_document(doc_id: str, file_path: str, file_ext: str, filename: str, file_size: int, file_hash: str = None):
    """后台处理文档：解析、向量化、索引。支持断点续传。"""
    meta = _load_metadata()
    checkpoint_path = _get_checkpoint_path(file_hash) if file_hash else None
    try:
        from ..core.config import settings

        chunks = None
        parse_method = "ragflow"
        if settings.RAGFLOW_ENABLED and file_ext != ".json":
            try:
                chunks = await _parse_via_ragflow(file_path, filename)
                logger.info(f"RAGFlow解析完成，共 {len(chunks)} 个片段")
            except Exception as e:
                logger.warning(f"RAGFlow解析失败: {e}")
                if settings.RAGFLOW_FALLBACK_TO_LOCAL:
                    chunks = None
                else:
                    raise
        if chunks is None:
            parse_method = "local"
            chunks = await _parse_file(file_path, file_ext, original_filename=filename, checkpoint_path=checkpoint_path, file_hash=file_hash)
            logger.info(f"本地解析完成，共 {len(chunks)} 个片段")

        # 先删除旧的 Milvus 数据（按 doc_id），再重新索引
        # 删除失败必须中止：静默继续会导致新旧 chunk 并存、检索结果重复
        try:
            from ..vectorstore.shared_store import get_shared_store
            store = get_shared_store()
            await store.delete_by_doc_id(doc_id)
        except Exception as e:
            logger.error(f"删除旧索引失败 (doc_id={doc_id})，中止重新索引: {e}", exc_info=True)
            raise RuntimeError(f"清理旧索引失败，已中止以避免数据重复: {e}") from e

        await _index_chunks(chunks, doc_id=doc_id)
        logger.info(f"文档已索引到知识库 (解析方式: {parse_method})")

        meta[doc_id]["status"] = "ready"
        meta[doc_id]["chunk_count"] = len(chunks)
        meta[doc_id]["parse_method"] = parse_method
        _save_metadata(meta)

        # 自动生成评测用例
        try:
            await _generate_eval_cases(doc_id, filename, chunks)
        except Exception as e:
            logger.warning(f"评测用例自动生成失败（不影响文档处理）: {e}")

        # 清理 checkpoint
        if checkpoint_path and os.path.exists(checkpoint_path):
            try:
                os.remove(checkpoint_path)
            except Exception:
                pass
    except Exception as e:
        import traceback
        logger.error(f"文档处理失败: {e}\n{traceback.format_exc()}")
        meta[doc_id]["status"] = "error"
        meta[doc_id]["error"] = str(e)
        _save_metadata(meta)


async def _generate_eval_cases(doc_id: str, filename: str, chunks):
    """文档处理完成后自动生成评测用例（存储到 PostgreSQL）"""
    from ..evaluation.case_generator import EvalCaseGenerator
    from ..evaluation.dataset import mark_stale_by_doc_db, add_cases_to_db
    from ..core.model_config import ModelManager
    from ..services.llm_service import LLMService
    from ..services import permission_service

    model_manager = ModelManager.from_env()
    llm_config = model_manager.get_best_available_model()
    if not llm_config:
        logger.info("LLM 未配置，跳过评测用例生成")
        return

    # 拿文档的 scope_ids，写入评测用例元数据便于分层评测
    scope_ids = await permission_service.get_doc_scopes(doc_id)

    llm = LLMService(
        api_key=llm_config.api_key,
        base_url=llm_config.api_base_url,
        model_name=llm_config.model_name,
        temperature=0.3,
    )

    # 提取 chunk 内容
    chunk_dicts = []
    for c in chunks:
        content = getattr(c, "content", "")
        if not content:
            continue
        chunk_dicts.append({
            "content": content,
            "metadata": {
                "source_file": getattr(c.metadata, "source_file", "") if hasattr(c, "metadata") else "",
                "content_type": getattr(c.metadata, "content_type", "") if hasattr(c, "metadata") else "",
            },
        })

    if not chunk_dicts:
        return

    # 标记旧用例为 stale
    marked = await mark_stale_by_doc_db(doc_id)
    if marked:
        logger.info(f"已标记 {marked} 条旧用例为失效 (doc_id={doc_id})")

    # 生成新用例
    generator = EvalCaseGenerator(llm_client=llm)
    new_cases = await generator.generate_from_chunks(doc_id, filename, chunk_dicts, scope_ids=scope_ids)

    if new_cases:
        await add_cases_to_db(new_cases)
        logger.info(f"评测用例更新完成: 新增 {len(new_cases)} 条 (doc_id={doc_id})")


@router.post("/upload", summary="上传文档")
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    scope_ids: str = Form(default="", description="逗号分隔的 scope_id 列表，如 'finance,hr'"),
    user: CurrentUser = Depends(get_current_user),
):
    from ..core.config import settings
    logger.info(f"收到上传请求: {file.filename}, 用户={user.username}, scope_ids={scope_ids}")
    allowed_extensions = [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".json", ".txt", ".md"]
    file_ext = "." + file.filename.split(".")[-1].lower() if "." in file.filename else ""
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file_ext}")

    # 解析并校验 scope_ids
    parsed_scopes = [s.strip() for s in scope_ids.split(",") if s.strip()]
    user_visible = await permission_service.get_visible_scopes(user.username)
    if user_visible is not None:
        # 非 admin：只能上传到自己可见的 scope
        invalid = [s for s in parsed_scopes if s not in user_visible]
        if invalid:
            raise HTTPException(status_code=403, detail=f"无权将文档分到 scope: {invalid}")
    if not parsed_scopes:
        # 未指定 scope 时默认归到 general（admin 上传时可能忘记选）
        parsed_scopes = ["general"]

    try:
        content = await file.read()
        max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件超过大小限制 {settings.MAX_FILE_SIZE_MB}MB")
        file_hash = _compute_file_hash(content)
        checkpoint_path = _get_checkpoint_path(file_hash)
        meta = _load_metadata()

        # 检查是否为同一文件的重新上传（有 checkpoint 或之前处理失败）
        existing_doc_id = None
        for did, doc in meta.items():
            if doc.get("file_hash") == file_hash and doc.get("filename") == file.filename:
                existing_doc_id = did
                break

        if existing_doc_id:
            doc_id = existing_doc_id
            file_path = meta[doc_id].get("file_path")
            # 如果文件不在了，重新保存
            if not file_path or not os.path.exists(file_path):
                upload_dir = settings.UPLOAD_DIR
                os.makedirs(upload_dir, exist_ok=True)
                file_path = _safe_upload_path(upload_dir, doc_id, file.filename)
                with open(file_path, "wb") as f:
                    f.write(content)
            meta[doc_id]["status"] = "processing"
            meta[doc_id]["error"] = None
            meta[doc_id]["upload_time"] = datetime.now().isoformat()
            _save_metadata(meta)
            logger.info(f"检测到同一文件重新上传，doc_id={doc_id}，从断点恢复处理")
        else:
            doc_id = str(uuid.uuid4())[:8]
            upload_dir = settings.UPLOAD_DIR
            os.makedirs(upload_dir, exist_ok=True)
            file_path = _safe_upload_path(upload_dir, doc_id, file.filename)
            with open(file_path, "wb") as f:
                f.write(content)
            meta[doc_id] = {
                "doc_id": doc_id, "filename": file.filename, "file_type": file_ext.lstrip("."),
                "file_size": len(content), "chunk_count": 0,
                "upload_time": datetime.now().isoformat(), "status": "processing",
                "file_path": file_path, "file_hash": file_hash,
            }
            _save_metadata(meta)

        # 写 doc_scopes 表（多对多绑定）
        await permission_service.set_doc_scopes(doc_id, parsed_scopes)
        logger.info(f"文档 {doc_id} 已绑定 scope: {parsed_scopes}")

        logger.info(f"文件已保存: {file_path}, hash={file_hash}")

        background_tasks.add_task(
            _process_document, doc_id, file_path, file_ext, file.filename, len(content), file_hash
        )

        msg = "文件上传成功，从断点恢复处理" if existing_doc_id else "文件上传成功，正在后台处理"
        return {
            "message": msg, "filename": file.filename, "status": "processing",
            "doc_id": doc_id, "resumed": bool(existing_doc_id),
            "scope_ids": parsed_scopes,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"文档上传失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"文档上传失败: {str(e)}")


async def _parse_file(file_path: str, file_ext: str, original_filename: str = None, checkpoint_path: str = None, file_hash: str = None):
    from ..tools.pdf_parser import PDFParserTool
    from ..tools.word_parser import WordParserTool
    from ..tools.excel_parser import ExcelParserTool
    from ..tools.csv_parser import CSVParserTool
    from ..tools.text_parser import TextParserTool
    from ..tools.json_parser import JSONParserTool
    from ..tools.text_splitter import SmartTextSplitter
    from ..services.llm_service import LLMService
    from ..core.config import settings

    llm_client = None
    if settings.PREMIUM_MODEL_API_KEY:
        llm_client = LLMService(
            api_key=settings.PREMIUM_MODEL_API_KEY,
            base_url=settings.PREMIUM_MODEL_API_BASE_URL,
            model_name=settings.PREMIUM_MODEL_NAME, temperature=0.3, max_tokens=1024,
        )
    if file_ext == ".pdf":
        parser = PDFParserTool(llm_client=llm_client)
    elif file_ext == ".docx":
        parser = WordParserTool(llm_client=llm_client)
    elif file_ext in (".xlsx", ".xls"):
        parser = ExcelParserTool()
    elif file_ext == ".csv":
        parser = CSVParserTool()
    elif file_ext in (".txt", ".md"):
        parser = TextParserTool()
    elif file_ext == ".json":
        parser = JSONParserTool()
    else:
        raise ValueError(f"不支持的文件格式: {file_ext}")
    if not parser.validate(file_path):
        raise ValueError(f"文件验证失败: {file_path}")

    # PDF 支持断点续传
    if file_ext == ".pdf" and checkpoint_path:
        # 更新进度：解析中
        if file_hash:
            _update_progress(file_hash, "parsing")
        try:
            async def _parse_and_chunk():
                elements = await parser.parse_with_checkpoint(
                    file_path, original_filename=original_filename, checkpoint_path=checkpoint_path,
                    on_page_done=lambda done, total: _update_progress(file_hash, "parsing", done, total) if file_hash else None,
                )
                return await parser._elements_to_chunks(elements, file_path, original_filename)

            # 解析+OCR 是纯同步重计算，放到工作线程执行，避免阻塞事件循环导致整个 API 无响应
            chunks = await asyncio.to_thread(asyncio.run, _parse_and_chunk())
        finally:
            getattr(parser, "close_doc", lambda: None)()
    elif file_ext in (".pdf", ".docx"):
        try:
            chunks = await asyncio.to_thread(
                asyncio.run, parser.parse(file_path, original_filename=original_filename)
            )
        finally:
            getattr(parser, "close_doc", lambda: None)()
    else:
        chunks = await asyncio.to_thread(parser.parse, file_path)
    if not chunks:
        raise ValueError("文件解析结果为空")
    logger.info(f"文件解析完成，原始chunks数量: {len(chunks)}")

    # 更新进度：向量化中
    if file_hash:
        _update_progress(file_hash, "indexing")

    splitter = SmartTextSplitter(chunk_size=settings.CHUNK_SIZE, chunk_overlap=settings.CHUNK_OVERLAP, min_chunk_size=settings.CHUNK_MIN_SIZE)
    split_chunks = splitter.split_by_heading(chunks)
    if not split_chunks:
        raise ValueError("文本分割结果为空")
    logger.info(f"文本分割完成，最终chunks数量: {len(split_chunks)}")
    return split_chunks


def _update_progress(file_hash: str, stage: str, pages_done: int = 0, pages_total: int = 0):
    """更新处理进度到 checkpoint 文件"""
    checkpoint_path = _get_checkpoint_path(file_hash)
    try:
        data = {}
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data["stage"] = stage
        data["pages_done"] = pages_done
        data["pages_total"] = pages_total
        with open(checkpoint_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


async def _parse_via_ragflow(file_path: str, original_filename: str):
    from ..services.ragflow_client import RAGFlowClient
    from ..core.config import settings
    client = RAGFlowClient()
    try:
        raw_chunks = await client.upload_and_parse(file_path, original_filename)
        if not raw_chunks:
            raise ValueError("RAGFlow返回的chunks为空")
        file_ext = os.path.splitext(original_filename)[1].lstrip(".").lower()
        chunks = _convert_ragflow_chunks(raw_chunks, original_filename, file_ext)
        return chunks
    finally:
        pass


def _convert_ragflow_chunks(raw_chunks: list, source_file: str, file_type: str):
    from ..tools.base_tool import DocumentChunk
    chunks = []
    for i, raw in enumerate(raw_chunks):
        content = raw.get("content", "").strip()
        if not content:
            continue
        positions = raw.get("available_positions", [])
        page_number = positions[0] if positions else None
        img_id = raw.get("img_id")
        content_type = "image" if img_id else "text"
        chunk = DocumentChunk.create(
            content=content, source_file=source_file, file_type=file_type,
            chunk_index=i, page_number=page_number, content_type=content_type,
        )
        chunks.append(chunk)
    return chunks


async def _index_chunks(chunks, doc_id: str = ""):
    from ..services.embedding_service import EmbeddingService
    from ..core.config import settings
    from ..core.model_config import ModelManager
    from ..vectorstore.shared_store import get_shared_store
    if not chunks:
        logger.warning("没有需要索引的chunks")
        return
    model_manager = ModelManager.from_env()
    embedding_config = model_manager.get_embedding()
    if not embedding_config:
        raise ValueError("Embedding配置未找到")
    embedding_service = EmbeddingService(embedding_config)
    store = get_shared_store()
    texts = []
    valid_chunks = []
    for c in chunks:
        if c.content and len(c.content.strip()) > 0:
            texts.append(c.content)
            valid_chunks.append(c)
    if not texts:
        return
    logger.info(f"正在生成 {len(texts)} 个文本的向量...")
    vectors = await embedding_service.embed_documents(texts)
    if not vectors:
        raise ValueError("向量生成失败，返回为空")
    milvus_chunks = []
    for i, (chunk, vector) in enumerate(zip(valid_chunks, vectors)):
        metadata = chunk.metadata
        milvus_chunks.append({
            "chunk_id": f"{hashlib.md5(metadata.source_file.encode()).hexdigest()[:8]}_{metadata.chunk_index}_{str(uuid.uuid4())[:6]}",
            "content": chunk.content[:65535],
            "vector": vector,
            "metadata": {
                "source_file": metadata.source_file or "unknown",
                "file_type": metadata.file_type or "unknown",
                "page_number": metadata.page_number or 0,
                "line_start": metadata.line_start or 0,
                "line_end": metadata.line_end or 0,
                "content_type": metadata.content_type or "text",
                "section_title": metadata.section_title or "",
                "chunk_index": metadata.chunk_index or i,
                "table_nl_description": (metadata.table_nl_description or "")[:65535],
            }
        })
    logger.info(f"正在将 {len(milvus_chunks)} 个片段存入Milvus...")
    await store.initialize()
    await store.insert(milvus_chunks, doc_id=doc_id)


@router.get("/{doc_id}/progress", summary="处理进度")
async def get_document_progress(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    meta = _load_metadata()
    doc = meta.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)
    if doc.get("status") != "processing":
        return {"status": doc.get("status"), "pages_done": 0, "pages_total": 0, "stage": "done"}
    file_hash = doc.get("file_hash")
    if not file_hash:
        return {"status": "processing", "pages_done": 0, "pages_total": 0, "stage": "unknown"}
    checkpoint_path = _get_checkpoint_path(file_hash)
    try:
        if os.path.exists(checkpoint_path):
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "status": "processing",
                "pages_done": data.get("pages_done", len(data.get("completed_pages", []))),
                "pages_total": data.get("pages_total", 0),
                "stage": data.get("stage", "parsing"),
            }
    except Exception:
        pass
    return {"status": "processing", "pages_done": 0, "pages_total": 0, "stage": "parsing"}


@router.get("/", response_model=DocumentListResponse, summary="文档列表")
async def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    file_type: Optional[str] = None,
    scope_filter: Optional[str] = Query(default=None, description="按 scope_id 过滤"),
    user: CurrentUser = Depends(get_current_user),
):
    meta = _load_metadata()
    all_docs = list(meta.values())

    # 一次查清：所有已绑定 scope 的 doc_id 集合
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        all_doc_ids = [d["doc_id"] for d in all_docs]
        bound_rows = await conn.fetch(
            "SELECT DISTINCT doc_id FROM doc_scopes WHERE doc_id = ANY($1)",
            all_doc_ids or ["__none__"],
        )
        bound_ids_set = {r["doc_id"] for r in bound_rows}

    # 1) 按用户角色可见 scope 过滤
    #    - admin (scope_ids 含 "all")：看全部
    #    - 普通用户：看自己 scope 内的 + 未绑定 scope 的历史文档（向后兼容）
    accessible_ids = await permission_service.get_accessible_doc_ids(user.username)
    if accessible_ids is not None:
        all_docs = [
            d for d in all_docs
            if d["doc_id"] in accessible_ids or d["doc_id"] not in bound_ids_set
        ]

    # 2) 前端下拉：进一步按具体 scope 筛选（仅在用户可见范围内）
    if scope_filter:
        async with pool.acquire() as conn:
            scope_rows = await conn.fetch(
                "SELECT doc_id FROM doc_scopes WHERE scope_id = $1", scope_filter
            )
            scope_doc_ids = {r["doc_id"] for r in scope_rows}
        all_docs = [d for d in all_docs if d["doc_id"] in scope_doc_ids]

    if file_type:
        all_docs = [d for d in all_docs if d.get("file_type") == file_type]
    all_docs.sort(key=lambda d: d.get("upload_time", ""), reverse=True)

    # 批量填充 scope_ids
    all_docs = await _enrich_with_scopes(all_docs)

    total = len(all_docs)
    start = (page - 1) * page_size
    end = start + page_size
    return DocumentListResponse(total=total, documents=all_docs[start:end])


@router.get("/{doc_id}", summary="文档详情")
async def get_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    meta = _load_metadata()
    doc = meta.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)
    doc = dict(doc)
    doc["scope_ids"] = await permission_service.get_doc_scopes(doc_id)
    return doc


@router.get("/{doc_id}/download", summary="下载文档")
async def download_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    from ..core.config import settings
    meta = _load_metadata()
    doc = meta.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)
    file_path = doc.get("file_path")
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    # 校验文件路径必须位于上传目录内，防止通过 metadata 中篡改的路径任意读文件
    upload_dir_abs = os.path.abspath(settings.UPLOAD_DIR)
    if not os.path.abspath(file_path).startswith(upload_dir_abs + os.sep):
        raise HTTPException(status_code=403, detail="非法的文件路径")
    return FileResponse(path=file_path, filename=doc.get("filename", os.path.basename(file_path)), media_type="application/octet-stream")


@router.delete("/{doc_id}", summary="删除文档")
async def delete_document(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    meta = _load_metadata()
    doc = meta.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)
    try:
        from ..vectorstore.shared_store import get_shared_store
        store = get_shared_store()
        await store.delete_by_doc_id(doc_id)
    except Exception as e:
        # 向量清理失败必须中止：继续删元数据/文件会产生「界面已删除、
        # 检索仍命中」的幽灵文档，且元数据丢失后无法重试
        logger.error(f"删除Milvus数据失败 (doc_id={doc_id})，中止删除: {e}")
        raise HTTPException(status_code=500, detail="向量索引清理失败，文档未删除，请重试")
    file_path = doc.get("file_path")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            logger.warning(f"删除文件失败: {e}")
    # 标记关联评测用例为失效
    try:
        from ..evaluation.dataset import mark_stale_by_doc_db
        marked = await mark_stale_by_doc_db(doc_id)
        if marked:
            logger.info(f"已标记 {marked} 条评测用例为失效 (doc_id={doc_id})")
    except Exception as e:
        logger.warning(f"标记评测用例失效失败: {e}")

    # 清理 doc_scopes 绑定
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM doc_scopes WHERE doc_id = $1", doc_id)

    del meta[doc_id]
    _save_metadata(meta)
    return {"message": "文档已删除", "doc_id": doc_id}


@router.post("/{doc_id}/reupload", summary="重新上传文档（覆盖更新）")
async def reupload_document(
    doc_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    from ..core.config import settings
    meta = _load_metadata()
    old_doc = meta.get(doc_id)
    if not old_doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)

    allowed_extensions = [".pdf", ".docx", ".xlsx", ".xls", ".csv", ".json", ".txt", ".md"]
    file_ext = "." + file.filename.split(".")[-1].lower() if "." in file.filename else ""
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file_ext}")

    try:
        content = await file.read()
        max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(status_code=413, detail=f"文件超过大小限制 {settings.MAX_FILE_SIZE_MB}MB")
        file_hash = _compute_file_hash(content)

        # 删除旧文件
        old_file_path = old_doc.get("file_path")
        if old_file_path and os.path.exists(old_file_path):
            try:
                os.remove(old_file_path)
            except Exception:
                pass

        # 保存新文件
        upload_dir = settings.UPLOAD_DIR
        os.makedirs(upload_dir, exist_ok=True)
        file_path = _safe_upload_path(upload_dir, doc_id, file.filename)
        with open(file_path, "wb") as f:
            f.write(content)

        # 更新 metadata
        file_type = file_ext.lstrip(".")
        meta[doc_id].update({
            "filename": file.filename,
            "file_type": file_type,
            "file_size": len(content),
            "file_path": file_path,
            "file_hash": file_hash,
            "status": "processing",
            "error": None,
            "upload_time": datetime.now().isoformat(),
        })
        _save_metadata(meta)

        background_tasks.add_task(
            _process_document, doc_id, file_path, file_ext, file.filename, len(content), file_hash
        )
        return {"message": "文件重新上传成功，正在后台处理", "doc_id": doc_id, "status": "processing"}
    except Exception as e:
        logger.error(f"重新上传失败: {e}")
        raise HTTPException(status_code=500, detail=f"重新上传失败: {str(e)}")


@router.get("/{doc_id}/chunks", summary="文档分块详情")
async def get_document_chunks(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    meta = _load_metadata()
    doc = meta.get(doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)
    try:
        from ..vectorstore.shared_store import get_shared_store
        store = get_shared_store()
        chunks = await store.query_by_file(doc.get("filename", ""))
        return {"doc_id": doc_id, "chunks": chunks, "total": len(chunks)}
    except Exception as e:
        logger.warning(f"查询chunks失败: {e}")
        return {"doc_id": doc_id, "chunks": [], "total": 0}


# ── 文档 scope 管理（admin 专属） ──

@router.get("/{doc_id}/scopes", summary="文档的 scope 列表")
async def get_doc_scope_list(doc_id: str, user: CurrentUser = Depends(get_current_user)):
    meta = _load_metadata()
    if doc_id not in meta:
        raise HTTPException(status_code=404, detail="文档不存在")
    await _check_doc_access(user, doc_id)
    return {"doc_id": doc_id, "scope_ids": await permission_service.get_doc_scopes(doc_id)}


class SetDocScopesRequest(BaseModel):
    scope_ids: List[str]


@router.put("/{doc_id}/scopes", summary="修改文档 scope（admin）")
async def set_doc_scope(
    doc_id: str,
    req: SetDocScopesRequest,
    user: CurrentUser = Depends(get_current_user),
):
    if user.role_id != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可修改文档 scope")
    meta = _load_metadata()
    if doc_id not in meta:
        raise HTTPException(status_code=404, detail="文档不存在")
    await permission_service.set_doc_scopes(doc_id, req.scope_ids)
    return {"doc_id": doc_id, "scope_ids": req.scope_ids}
