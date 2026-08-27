"""
PDF文件解析器 - 保留精确位置信息
"""

from typing import List, Dict, Optional, Callable, Any
from .base_tool import BaseParserTool, DocumentChunk, DocumentElement, ChunkMetadata
import os
import json
import logging

logger = logging.getLogger(__name__)

# PaddleOCR 单例：模型加载极重（数百MB、秒级），严禁每页/每图重新实例化
_OCR_INSTANCE = None


def _get_paddle_ocr():
    global _OCR_INSTANCE
    if _OCR_INSTANCE is None:
        from paddleocr import PaddleOCR
        logger.info("初始化 PaddleOCR 单例（仅首次）...")
        _OCR_INSTANCE = PaddleOCR(use_angle_cls=True, lang="ch")
    return _OCR_INSTANCE


class PDFParserTool(BaseParserTool):
    """PDF解析器 - 支持文本和表格提取，保留页码和行号"""

    def __init__(self, llm_client=None, **kwargs):
        self.llm = llm_client
        from .table_processor import TableProcessor
        self.processor = TableProcessor(**kwargs)
        self._open_doc = None

    def close_doc(self):
        """异常路径兜底：关闭仍打开的 fitz 文档句柄（成功路径已正常 close，重复调用安全）。"""
        doc, self._open_doc = self._open_doc, None
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass

    @property
    def supported_extensions(self) -> List[str]:
        return [".pdf"]

    def validate(self, file_path: str) -> bool:
        return file_path.lower().endswith(".pdf") and os.path.exists(file_path)

    async def parse(self, file_path: str, original_filename: Optional[str] = None) -> List[DocumentChunk]:
        elements = self.parse_with_structure(file_path)
        return await self._elements_to_chunks(elements, file_path, original_filename)

    async def parse_with_checkpoint(
        self,
        file_path: str,
        original_filename: Optional[str] = None,
        checkpoint_path: Optional[str] = None,
        on_page_done: Optional[Callable[[int, int], Any]] = None,
    ) -> List[DocumentElement]:
        """支持断点续传的解析方法。每页处理完保存 checkpoint，可从中断处恢复。"""
        import fitz

        checkpoint = {"completed_pages": [], "page_results": {}}
        if checkpoint_path and os.path.exists(checkpoint_path):
            try:
                with open(checkpoint_path, "r", encoding="utf-8") as f:
                    checkpoint = json.load(f)
                logger.info(f"加载checkpoint，已完成 {len(checkpoint['completed_pages'])} 页")
            except Exception:
                pass

        completed = set(checkpoint.get("completed_pages", []))
        page_results = checkpoint.get("page_results", {})

        doc = fitz.open(file_path)
        self._open_doc = doc
        total_pages = len(doc)
        elements = []

        # 先从 checkpoint 恢复已完成的页面
        for pn_str in sorted(page_results.keys(), key=int):
            for item in page_results[pn_str]:
                elements.append(DocumentElement(
                    type=item.get("type", "paragraph"),
                    text=item.get("text", ""),
                    page_number=int(pn_str),
                    line_number=item.get("line_number"),
                    metadata=item.get("metadata", {"source": "ocr"}),
                ))

        # 处理未完成的页面
        for page_num in range(total_pages):
            pn = page_num + 1  # 1-based
            if pn in completed:
                continue

            page = doc[page_num]
            page_elements = []

            blocks = page.get_text("dict")["blocks"]
            page_texts = []
            for block in blocks:
                if block["type"] == 0:
                    for line in block["lines"]:
                        text = " ".join([span["text"] for span in line["spans"]])
                        if text.strip():
                            page_texts.append(text.strip())
            combined_text = "\n".join(page_texts)
            use_ocr = self._is_text_garbled(combined_text) and len(combined_text) > 10

            if use_ocr:
                logger.info(f"Page {pn}: 文本提取为乱码，降级为页面OCR")
                ocr_text = self._ocr_page(page)
                if ocr_text.strip():
                    for line_text in ocr_text.split("\n"):
                        line_text = line_text.strip()
                        if line_text:
                            is_heading = self._is_heading_from_text(line_text)
                            elem = DocumentElement(
                                type="heading" if is_heading else "paragraph",
                                text=line_text,
                                page_number=pn,
                                line_number=None,
                                metadata={"source": "ocr"},
                            )
                            page_elements.append(elem)
                            elements.append(elem)
                    logger.info(f"Page {pn}: OCR提取 {len(ocr_text)} 字符")
                else:
                    logger.warning(f"Page {pn}: OCR也未能提取到文本")
            else:
                line_counter = 0
                for block in blocks:
                    if block["type"] == 0:
                        for line in block["lines"]:
                            line_counter += 1
                            text = " ".join([span["text"] for span in line["spans"]])
                            if text.strip():
                                is_heading = self._is_heading(line)
                                section_title = text.strip() if is_heading else None
                                elem = DocumentElement(
                                    type="heading" if is_heading else "paragraph",
                                    text=text.strip(),
                                    page_number=pn,
                                    line_number=line_counter,
                                    metadata={"bbox": line["bbox"], "section_title": section_title},
                                )
                                page_elements.append(elem)
                                elements.append(elem)
                    elif block["type"] == 1:
                        bbox = block.get("bbox", (0, 0, 0, 0))
                        bbox_w = bbox[2] - bbox[0]
                        bbox_h = bbox[3] - bbox[1]
                        if bbox_w < 30 or bbox_h < 30:
                            continue
                        elem = DocumentElement(
                            type="image",
                            page_number=pn,
                            line_number=line_counter + 1,
                            image_data=block.get("image"),
                            metadata={"bbox": bbox, "width": block.get("width"), "height": block.get("height")},
                        )
                        page_elements.append(elem)
                        elements.append(elem)

            tables = self._extract_tables_from_page(page, pn)
            page_elements.extend(tables)
            elements.extend(tables)

            # 保存这一页的结果到 checkpoint
            page_results[str(pn)] = [
                {"type": e.type, "text": e.text, "page_number": e.page_number,
                 "line_number": e.line_number, "metadata": e.metadata if isinstance(e.metadata, dict) else {}}
                for e in page_elements
            ]
            completed.add(pn)

            if checkpoint_path:
                try:
                    os.makedirs(os.path.dirname(checkpoint_path), exist_ok=True)
                    with open(checkpoint_path, "w", encoding="utf-8") as f:
                        json.dump({
                            "completed_pages": sorted(completed, key=int),
                            "page_results": page_results,
                            "total_pages": total_pages,
                        }, f, ensure_ascii=False)
                except Exception as e:
                    logger.warning(f"保存checkpoint失败: {e}")

            if on_page_done:
                try:
                    on_page_done(pn, total_pages)
                except Exception:
                    pass

        self.close_doc()
        return elements

    @staticmethod
    def _strip_uuid_prefix(filename: str) -> str:
        basename = os.path.basename(filename)
        if "_" in basename:
            prefix = basename.split("_", 1)[0]
            if len(prefix) == 8 and all(c in "0123456789abcdef" for c in prefix):
                return basename.split("_", 1)[1]
        return basename

    @staticmethod
    def _is_text_garbled(text: str) -> bool:
        if not text or len(text.strip()) < 5:
            return True
        text = text.strip()
        cjk_count = sum(1 for ch in text if 0x4E00 <= ord(ch) <= 0x9FFF or 0x3400 <= ord(ch) <= 0x4DBF)
        cjk_ratio = cjk_count / len(text) if text else 0
        if cjk_ratio < 0.05 and len(text) > 20:
            return True
        control_count = sum(1 for ch in text if ord(ch) < 0x20 and ch not in '\n\t\r')
        private_use_count = sum(1 for ch in text if 0xE000 <= ord(ch) <= 0xF8FF)
        if (control_count + private_use_count) / max(len(text), 1) > 0.1:
            return True
        return False

    def _extract_text_from_image(self, image_data: bytes) -> str:
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(image_data)
            temp_path = f.name
        try:
            try:
                ocr = _get_paddle_ocr()
                result = ocr.ocr(temp_path, cls=True)
                if result and result[0]:
                    text = "\n".join([line[1][0] for line in result[0]])
                    if text.strip():
                        return text
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"PaddleOCR图片OCR失败: {e}")
            try:
                import pytesseract
                from PIL import Image
                image = Image.open(temp_path)
                text = pytesseract.image_to_string(image, lang='chi_sim+eng')
                if text.strip():
                    return text
            except (ImportError, Exception):
                pass
            try:
                import fitz
                img_doc = fitz.open(temp_path)
                if img_doc.page_count > 0:
                    text = img_doc[0].get_text()
                    if text.strip():
                        return text.strip()
                img_doc.close()
            except Exception:
                pass
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return ""

    def _ocr_page(self, page) -> str:
        import fitz
        import tempfile
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_data = pix.tobytes("png")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(img_data)
            temp_path = f.name
        try:
            try:
                ocr = _get_paddle_ocr()
                result = ocr.ocr(temp_path, cls=True)
                if result and result[0]:
                    lines = []
                    for line in result[0]:
                        text = line[1][0]
                        confidence = line[1][1]
                        if confidence > 0.5 and text.strip():
                            lines.append(text)
                    if lines:
                        return "\n".join(lines)
            except ImportError:
                pass
            except Exception as e:
                logger.warning(f"PaddleOCR页面OCR失败: {e}")
            try:
                import pytesseract
                from PIL import Image
                image = Image.open(temp_path)
                text = pytesseract.image_to_string(image, lang='chi_sim+eng')
                if text.strip():
                    return text
            except (ImportError, Exception) as e:
                logger.debug(f"Tesseract page OCR failed: {e}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return ""

    async def _describe_image_with_llm(self, image_data: bytes, context: str = "") -> str:
        if not self.llm:
            return ""
        try:
            prompt = "请详细描述这张图片的内容。如果图片中有人物，请描述其外貌特征；如果有文字，请提取所有文字内容；如果有设备或场景，请描述其特征。"
            if context:
                prompt += f"\n上下文信息：{context}"
            description = await self.llm.generate_with_image(prompt, image_data)
            return description
        except Exception as e:
            logger.warning(f"LLM图片描述失败: {e}")
            return ""

    def parse_with_structure(self, file_path: str) -> List[DocumentElement]:
        import fitz
        doc = fitz.open(file_path)
        self._open_doc = doc
        elements = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]
            page_texts = []
            for block in blocks:
                if block["type"] == 0:
                    for line in block["lines"]:
                        text = " ".join([span["text"] for span in line["spans"]])
                        if text.strip():
                            page_texts.append(text.strip())
            combined_text = "\n".join(page_texts)
            use_ocr = self._is_text_garbled(combined_text) and len(combined_text) > 10
            if use_ocr:
                logger.info(f"Page {page_num + 1}: 文本提取为乱码，降级为页面OCR")
                ocr_text = self._ocr_page(page)
                if ocr_text.strip():
                    for line_idx, line_text in enumerate(ocr_text.split("\n")):
                        line_text = line_text.strip()
                        if line_text:
                            is_heading = self._is_heading_from_text(line_text)
                            elements.append(DocumentElement(
                                type="heading" if is_heading else "paragraph",
                                text=line_text,
                                page_number=page_num + 1,
                                line_number=None,
                                metadata={"source": "ocr"}
                            ))
                    logger.info(f"Page {page_num + 1}: OCR提取 {len(ocr_text)} 字符")
                else:
                    logger.warning(f"Page {page_num + 1}: OCR也未能提取到文本")
            else:
                line_counter = 0
                for block in blocks:
                    if block["type"] == 0:
                        for line in block["lines"]:
                            line_counter += 1
                            text = " ".join([span["text"] for span in line["spans"]])
                            if text.strip():
                                is_heading = self._is_heading(line)
                                section_title = text.strip() if is_heading else None
                                elements.append(DocumentElement(
                                    type="heading" if is_heading else "paragraph",
                                    text=text.strip(),
                                    page_number=page_num + 1,
                                    line_number=line_counter,
                                    metadata={"bbox": line["bbox"], "section_title": section_title}
                                ))
                    elif block["type"] == 1:
                        bbox = block.get("bbox", (0, 0, 0, 0))
                        bbox_w = bbox[2] - bbox[0]
                        bbox_h = bbox[3] - bbox[1]
                        if bbox_w < 30 or bbox_h < 30:
                            continue
                        elements.append(DocumentElement(
                            type="image",
                            page_number=page_num + 1,
                            line_number=line_counter + 1,
                            image_data=block.get("image"),
                            metadata={"bbox": bbox, "width": block.get("width"), "height": block.get("height")}
                        ))
            tables = self._extract_tables_from_page(page, page_num + 1)
            elements.extend(tables)
        self.close_doc()
        return elements

    def _is_heading_from_text(self, text: str) -> bool:
        import re
        if re.match(r'^[\d]+[\.\、][\d]*[\.\、]?\s*\S', text):
            return True
        heading_keywords = ["前言", "范围", "规范性引用", "术语", "定义", "总则", "一般要求", "附录", "参考文献", "目次"]
        for kw in heading_keywords:
            if text.strip().startswith(kw):
                return True
        return False

    def _is_heading(self, line: Dict) -> bool:
        if not line.get("spans"):
            return False
        for span in line["spans"]:
            font_size = span.get("size", 0)
            if font_size >= 14:
                return True
            font_name = span.get("font", "").lower()
            if "bold" in font_name or "黑体" in font_name:
                return True
        return False

    def _extract_tables_from_page(self, page, page_num: int) -> List[DocumentElement]:
        elements = []
        try:
            tables = page.find_tables()
            for table_idx, table in enumerate(tables):
                headers = []
                rows = []
                for row_idx, row in enumerate(table.extract()):
                    if row_idx == 0:
                        headers = [cell.strip() if cell else "" for cell in row]
                    else:
                        rows.append([cell.strip() if cell else "" for cell in row])
                if headers and rows:
                    elements.append(DocumentElement(
                        type="table",
                        headers=headers,
                        rows=rows,
                        page_number=page_num,
                        metadata={"table_index": table_idx, "bbox": table.bbox}
                    ))
        except Exception:
            pass
        return elements

    async def _elements_to_chunks(
        self,
        elements: List[DocumentElement],
        source_file: str,
        original_filename: Optional[str] = None
    ) -> List[DocumentChunk]:
        chunks = []
        current_text = ""
        current_metadata = {}
        chunk_index = 0
        current_section = None
        if original_filename:
            file_name = original_filename
        else:
            file_name = self._strip_uuid_prefix(source_file)
        file_type = "pdf"
        for element in elements:
            if element.type == "heading":
                if current_text.strip():
                    chunks.append(DocumentChunk.create(
                        content=current_text.strip(), source_file=file_name, file_type=file_type,
                        chunk_index=chunk_index, page_number=current_metadata.get("page_number"),
                        line_start=current_metadata.get("line_start"), line_end=current_metadata.get("line_end"),
                        section_title=current_section, content_type="text"
                    ))
                    chunk_index += 1
                    current_text = ""
                    current_metadata = {}
                current_section = element.text
                continue
            if element.page_number != current_metadata.get("page_number") and current_text.strip():
                chunks.append(DocumentChunk.create(
                    content=current_text.strip(), source_file=file_name, file_type=file_type,
                    chunk_index=chunk_index, page_number=current_metadata.get("page_number"),
                    line_start=current_metadata.get("line_start"), line_end=current_metadata.get("line_end"),
                    section_title=current_section, content_type="text"
                ))
                chunk_index += 1
                current_text = ""
                current_metadata = {}
            if element.type == "table":
                if current_text:
                    chunks.append(DocumentChunk.create(
                        content=current_text.strip(), source_file=file_name, file_type=file_type,
                        chunk_index=chunk_index, page_number=current_metadata.get("page_number"),
                        line_start=current_metadata.get("line_start"), line_end=current_metadata.get("line_end"),
                        section_title=current_section, content_type="text"
                    ))
                    chunk_index += 1
                    current_text = ""
                table_chunks = self.processor.process_table(
                    headers=element.headers, rows=element.rows,
                    source_file=file_name, file_type=file_type,
                    chunk_index=chunk_index, page_number=element.page_number,
                    section_title=current_section,
                )
                chunks.extend(table_chunks)
                chunk_index += len(table_chunks)
            elif element.type == "image":
                if current_text:
                    chunks.append(DocumentChunk.create(
                        content=current_text.strip(), source_file=file_name, file_type=file_type,
                        chunk_index=chunk_index, page_number=current_metadata.get("page_number"),
                        line_start=current_metadata.get("line_start"), section_title=current_section,
                        content_type="text"
                    ))
                    chunk_index += 1
                    current_text = ""
                image_content = "[图片内容待提取]"
                if element.image_data:
                    try:
                        ocr_text = self._extract_text_from_image(element.image_data)
                        if ocr_text:
                            image_content = f"[图片OCR文字]\n{ocr_text}"
                            logger.info(f"图片OCR提取成功，page {element.page_number}，{len(ocr_text)} 字符")
                        else:
                            logger.info(f"图片OCR未提取到文字，尝试LLM描述，page {element.page_number}")
                            llm_desc = await self._describe_image_with_llm(element.image_data)
                            if llm_desc:
                                image_content = f"[图片内容]\n{llm_desc}"
                                logger.info(f"LLM图片描述成功，page {element.page_number}，{len(llm_desc)} 字符")
                    except Exception as e:
                        logger.warning(f"图片处理失败: {e}")
                chunks.append(DocumentChunk.create(
                    content=image_content, source_file=file_name, file_type=file_type,
                    chunk_index=chunk_index, page_number=element.page_number,
                    section_title=current_section, content_type="image"
                ))
                chunk_index += 1
            else:
                current_text += element.text + "\n"
                if not current_metadata:
                    current_metadata = {"page_number": element.page_number, "line_start": element.line_number}
                current_metadata["line_end"] = element.line_number
        if current_text.strip():
            chunks.append(DocumentChunk.create(
                content=current_text.strip(), source_file=file_name, file_type=file_type,
                chunk_index=chunk_index, page_number=current_metadata.get("page_number"),
                line_start=current_metadata.get("line_start"), line_end=current_metadata.get("line_end"),
                section_title=current_section, content_type="text"
            ))
        return chunks
