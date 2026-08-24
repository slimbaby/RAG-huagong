"""
文本文件解析器
"""

from typing import List
from .base_tool import BaseParserTool, DocumentChunk, DocumentElement
import os


class TextParserTool(BaseParserTool):
    """文本文件解析器 - 支持.txt和.md文件"""

    @property
    def supported_extensions(self) -> List[str]:
        return [".txt", ".md"]

    def validate(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.supported_extensions and os.path.exists(file_path)

    def parse(self, file_path: str) -> List[DocumentChunk]:
        elements = self.parse_with_structure(file_path)
        return self._elements_to_chunks(elements, file_path)

    def parse_with_structure(self, file_path: str) -> List[DocumentElement]:
        import chardet
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            encoding = result['encoding'] or 'utf-8'
        with open(file_path, 'r', encoding=encoding) as f:
            lines = f.readlines()
        elements = []
        ext = os.path.splitext(file_path)[1].lower()
        for line_num, line in enumerate(lines, 1):
            text = line.strip()
            if not text:
                continue
            if ext == ".md" and text.startswith("#"):
                level = len(text.split(" ")[0])
                title = text.lstrip("#").strip()
                elements.append(DocumentElement(
                    type="heading", text=title, line_number=line_num,
                    metadata={"heading_level": level, "original": text}
                ))
            else:
                elements.append(DocumentElement(type="paragraph", text=text, line_number=line_num))
        return elements

    def _elements_to_chunks(self, elements: List[DocumentElement], source_file: str) -> List[DocumentChunk]:
        chunks = []
        current_text = ""
        current_metadata = {}
        chunk_index = 0
        current_section = None
        file_name = os.path.basename(source_file)
        file_type = os.path.splitext(file_name)[1].lstrip(".")
        for element in elements:
            if element.type == "heading":
                if current_text.strip():
                    chunks.append(DocumentChunk.create(
                        content=current_text.strip(), source_file=file_name, file_type=file_type,
                        chunk_index=chunk_index, line_start=current_metadata.get("line_start"),
                        line_end=current_metadata.get("line_end"), section_title=current_section,
                        content_type="text"
                    ))
                    chunk_index += 1
                    current_text = ""
                current_section = element.text
            else:
                current_text += element.text + "\n"
                if not current_metadata:
                    current_metadata = {"line_start": element.line_number}
                current_metadata["line_end"] = element.line_number
        if current_text.strip():
            chunks.append(DocumentChunk.create(
                content=current_text.strip(), source_file=file_name, file_type=file_type,
                chunk_index=chunk_index, line_start=current_metadata.get("line_start"),
                line_end=current_metadata.get("line_end"), section_title=current_section,
                content_type="text"
            ))
        return chunks
