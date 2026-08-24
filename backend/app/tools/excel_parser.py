"""
Excel文件解析器
"""

from typing import List
from .base_tool import BaseParserTool, DocumentChunk, DocumentElement
from .table_processor import TableProcessor
import os


class ExcelParserTool(BaseParserTool):
    """Excel解析器 - 支持.xlsx和.xls文件"""

    def __init__(self, **kwargs):
        self.processor = TableProcessor(**kwargs)

    @property
    def supported_extensions(self) -> List[str]:
        return [".xlsx", ".xls"]

    def validate(self, file_path: str) -> bool:
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self.supported_extensions and os.path.exists(file_path)

    def parse(self, file_path: str) -> List[DocumentChunk]:
        elements = self.parse_with_structure(file_path)
        return self._elements_to_chunks(elements, file_path)

    def parse_with_structure(self, file_path: str) -> List[DocumentElement]:
        import pandas as pd
        elements = []
        excel_file = pd.ExcelFile(file_path)
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            if df.empty:
                continue
            headers = [str(col) for col in df.columns.tolist()]
            rows = []
            for _, row in df.iterrows():
                row_data = [str(val) if pd.notna(val) else "" for val in row.tolist()]
                rows.append(row_data)
            if headers and rows:
                elements.append(DocumentElement(
                    type="table", headers=headers, rows=rows,
                    metadata={"sheet_name": sheet_name, "row_count": len(rows), "col_count": len(headers)}
                ))
        return elements

    def _elements_to_chunks(self, elements: List[DocumentElement], source_file: str) -> List[DocumentChunk]:
        chunks = []
        chunk_index = 0
        file_name = os.path.basename(source_file)
        file_type = os.path.splitext(file_name)[1].lstrip(".")
        for element in elements:
            if element.type == "table":
                sheet_name = element.metadata.get("sheet_name", "Sheet1")
                table_chunks = self.processor.process_table(
                    headers=element.headers,
                    rows=element.rows,
                    source_file=file_name,
                    file_type=file_type,
                    chunk_index=chunk_index,
                    section_title=f"工作表: {sheet_name}",
                )
                chunks.extend(table_chunks)
                chunk_index += len(table_chunks)
        return chunks
