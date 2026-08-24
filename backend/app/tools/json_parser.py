"""
JSON文件解析器
"""

from typing import List, Dict, Any
from .base_tool import BaseParserTool, DocumentChunk, DocumentElement
import os
import json


class JSONParserTool(BaseParserTool):
    """JSON解析器"""

    @property
    def supported_extensions(self) -> List[str]:
        return [".json"]

    def validate(self, file_path: str) -> bool:
        return file_path.lower().endswith(".json") and os.path.exists(file_path)

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
            data = json.load(f)
        elements = []
        self._extract_elements(data, elements, path="")
        return elements

    def _extract_elements(self, data: Any, elements: List[DocumentElement], path: str, depth: int = 0):
        if isinstance(data, dict):
            for key, value in data.items():
                current_path = f"{path}.{key}" if path else key
                if isinstance(value, (dict, list)):
                    self._extract_elements(value, elements, current_path, depth + 1)
                else:
                    text = f"{key}: {value}"
                    elements.append(DocumentElement(
                        type="paragraph", text=text,
                        metadata={"json_path": current_path, "key": key, "value": str(value), "depth": depth}
                    ))
        elif isinstance(data, list):
            for idx, item in enumerate(data):
                current_path = f"{path}[{idx}]"
                if isinstance(item, (dict, list)):
                    self._extract_elements(item, elements, current_path, depth + 1)
                else:
                    text = str(item)
                    elements.append(DocumentElement(
                        type="paragraph", text=text,
                        metadata={"json_path": current_path, "index": idx, "depth": depth}
                    ))

    def _elements_to_chunks(self, elements: List[DocumentElement], source_file: str) -> List[DocumentChunk]:
        chunks = []
        chunk_index = 0
        file_name = os.path.basename(source_file)
        groups = self._group_by_path(elements)
        for group_path, group_elements in groups.items():
            texts = [elem.text for elem in group_elements if elem.text]
            content = "\n".join(texts)
            if content.strip():
                chunks.append(DocumentChunk.create(
                    content=content.strip(), source_file=file_name, file_type="json",
                    chunk_index=chunk_index, content_type="text",
                    section_title=group_path if group_path else None
                ))
                chunk_index += 1
        return chunks

    def _group_by_path(self, elements: List[DocumentElement]) -> Dict[str, List[DocumentElement]]:
        groups: Dict[str, List[DocumentElement]] = {}
        for element in elements:
            json_path = element.metadata.get("json_path", "")
            parts = json_path.split(".")
            group_key = parts[0] if parts else ""
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(element)
        return groups
