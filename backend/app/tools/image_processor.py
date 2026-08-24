"""
图片处理器 - 多模态理解 + OCR降级
"""

from typing import Optional, Dict, List
from .base_tool import DocumentChunk
import os
import tempfile


class ImageProcessor:
    """图片处理器 - 多模态LLM + OCR双模式"""

    def __init__(self, llm_client=None, ocr_engine: str = "paddleocr"):
        self.llm = llm_client
        self.ocr_engine = ocr_engine
        self._ocr = None

    def _get_ocr(self):
        if self._ocr is None:
            if self.ocr_engine == "paddleocr":
                try:
                    from paddleocr import PaddleOCR
                    self._ocr = PaddleOCR(use_angle_cls=True, lang="ch")
                except ImportError:
                    self._ocr = "paddleocr_unavailable"
            elif self.ocr_engine == "tesseract":
                try:
                    import pytesseract
                    self._ocr = "tesseract"
                except ImportError:
                    self._ocr = "tesseract_unavailable"
        return self._ocr

    async def process_image(self, image_data: bytes, source_file: str, chunk_index: int = 0, page_number: Optional[int] = None, context: str = "") -> DocumentChunk:
        temp_path = self._save_temp_image(image_data)
        try:
            if self.llm:
                try:
                    description = await self._describe_with_llm(temp_path, context)
                    ocr_text = self._extract_text_ocr(temp_path)
                    content = f"[图片内容] {description}"
                    if ocr_text:
                        content += f"\n[图中文字] {ocr_text}"
                    if context:
                        content += f"\n[上下文] {context}"
                    return DocumentChunk.create(content=content, source_file=source_file, file_type="image", chunk_index=chunk_index, page_number=page_number, content_type="image")
                except Exception:
                    pass
            ocr_text = self._extract_text_ocr(temp_path)
            content = f"[OCR识别文字] {ocr_text}" if ocr_text else "[图片内容待识别]"
            if context:
                content += f"\n[上下文] {context}"
            return DocumentChunk.create(content=content, source_file=source_file, file_type="image", chunk_index=chunk_index, page_number=page_number, content_type="image")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def _describe_with_llm(self, image_path: str, context: str) -> str:
        if not self.llm:
            raise ValueError("LLM client not available")
        prompt = f"请描述这张图片的内容，特别是与化工园区相关的信息。{f'上下文信息：{context}' if context else ''}"
        response = await self.llm.generate_with_image(prompt, image_path)
        return response

    def _extract_text_ocr(self, image_path: str) -> str:
        ocr = self._get_ocr()
        if ocr == "paddleocr_unavailable" or ocr == "tesseract_unavailable":
            return ""
        try:
            if self.ocr_engine == "paddleocr" and ocr != "paddleocr_unavailable":
                result = ocr.ocr(image_path, cls=True)
                if result and result[0]:
                    return "\n".join([line[1][0] for line in result[0]])
            elif self.ocr_engine == "tesseract" and ocr != "tesseract_unavailable":
                from PIL import Image
                import pytesseract
                image = Image.open(image_path)
                return pytesseract.image_to_string(image, lang='chi_sim+eng')
        except Exception:
            pass
        return ""

    def _save_temp_image(self, image_data: bytes) -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
            f.write(image_data)
            return f.name

    def extract_table_from_image(self, image_path: str) -> Optional[List[List[str]]]:
        ocr = self._get_ocr()
        if ocr == "paddleocr_unavailable":
            return None
        try:
            if self.ocr_engine == "paddleocr":
                result = ocr.ocr(image_path, cls=True)
                if result and result[0]:
                    lines = [line[1][0] for line in result[0]]
                    return [[line] for line in lines]
        except Exception:
            pass
        return None
