"""
文件解析工具模块
"""

from .base_tool import BaseParserTool, DocumentChunk, ChunkMetadata
from .pdf_parser import PDFParserTool
from .word_parser import WordParserTool
from .excel_parser import ExcelParserTool
from .csv_parser import CSVParserTool
from .json_parser import JSONParserTool
from .text_parser import TextParserTool
from .text_splitter import SmartTextSplitter
from .table_processor import TableProcessor
from .image_processor import ImageProcessor

PARSER_REGISTRY = {
    ".pdf": PDFParserTool,
    ".docx": WordParserTool,
    ".xlsx": ExcelParserTool,
    ".xls": ExcelParserTool,
    ".csv": CSVParserTool,
    ".json": JSONParserTool,
    ".txt": TextParserTool,
    ".md": TextParserTool,
}


def get_parser(file_path: str) -> BaseParserTool:
    import os
    ext = os.path.splitext(file_path)[1].lower()
    parser_class = PARSER_REGISTRY.get(ext)
    if not parser_class:
        raise ValueError(f"Unsupported file type: {ext}")
    return parser_class()


__all__ = [
    "BaseParserTool", "DocumentChunk", "ChunkMetadata",
    "PDFParserTool", "WordParserTool", "ExcelParserTool",
    "CSVParserTool", "JSONParserTool", "TextParserTool",
    "SmartTextSplitter", "TableProcessor", "ImageProcessor",
    "PARSER_REGISTRY", "get_parser",
]
