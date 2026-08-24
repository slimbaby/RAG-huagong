"""
CSV文件解析器
"""

from typing import List
from .base_tool import BaseParserTool, DocumentChunk, DocumentElement
from .table_processor import TableProcessor
import os


class CSVParserTool(BaseParserTool):
    """CSV解析器"""

    def __init__(self, **kwargs):
        self.processor = TableProcessor(**kwargs)

    @property
    def supported_extensions(self) -> List[str]:
        return [".csv"]

    def validate(self, file_path: str) -> bool:
        return file_path.lower().endswith(".csv") and os.path.exists(file_path)

    def parse(self, file_path: str) -> List[DocumentChunk]:
        elements = self.parse_with_structure(file_path)
        return self._elements_to_chunks(elements, file_path)

    def parse_with_structure(self, file_path: str) -> List[DocumentElement]:
        import pandas as pd
        import chardet

        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            encoding = result['encoding'] or 'utf-8'

        try:
            df = pd.read_csv(file_path, encoding=encoding)
        except UnicodeDecodeError:
            for enc in ['gbk', 'gb2312', 'gb18030', 'latin1']:
                try:
                    df = pd.read_csv(file_path, encoding=enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError(f"无法识别CSV文件编码: {file_path}")

        elements = []
        if not df.empty:
            headers = [str(col) for col in df.columns.tolist()]
            rows = []
            for _, row in df.iterrows():
                row_data = [str(val) if pd.notna(val) else "" for val in row.tolist()]
                rows.append(row_data)
            elements.append(DocumentElement(
                type="table", headers=headers, rows=rows,
                metadata={"row_count": len(rows), "col_count": len(headers)}
            ))
        return elements

    def _elements_to_chunks(self, elements: List[DocumentElement], source_file: str) -> List[DocumentChunk]:
        chunks = []
        chunk_index = 0
        file_name = os.path.basename(source_file)
        for element in elements:
            if element.type == "table":
                table_chunks = self.processor.process_table(
                    headers=element.headers,
                    rows=element.rows,
                    source_file=file_name,
                    file_type="csv",
                    chunk_index=chunk_index,
                )
                chunks.extend(table_chunks)
                chunk_index += len(table_chunks)
        return chunks
