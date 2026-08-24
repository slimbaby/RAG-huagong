"""
文件解析工具基类
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from datetime import datetime
import hashlib


class ChunkMetadata(BaseModel):
    """Chunk元数据 - 支持精确追溯"""
    chunk_id: str = Field(description="唯一标识")
    source_file: str = Field(description="源文件名")
    file_type: str = Field(description="文件类型")
    page_number: Optional[int] = Field(default=None, description="页码")
    line_start: Optional[int] = Field(default=None, description="起始行号")
    line_end: Optional[int] = Field(default=None, description="结束行号")
    paragraph_index: Optional[int] = Field(default=None, description="段落索引")
    section_title: Optional[str] = Field(default=None, description="所属章节标题")
    content_type: str = Field(default="text", description="内容类型 (text/table/image)")
    chunk_index: int = Field(default=0, description="在文档中的chunk序号")
    total_chunks: int = Field(default=0, description="文档总chunk数")
    created_at: datetime = Field(default_factory=datetime.now, description="创建时间")

    source_type: Optional[str] = Field(default=None, description="来源类型 (law_regulation/internal_doc)")
    document_number: Optional[str] = Field(default=None, description="文号")
    publish_date: Optional[str] = Field(default=None, description="发布日期")
    effective_date: Optional[str] = Field(default=None, description="生效日期")
    version: Optional[int] = Field(default=None, description="版本号")
    last_updated_at: Optional[str] = Field(default=None, description="最近更新时间")
    table_nl_description: Optional[str] = Field(default=None, description="表格自然语言描述")


class DocumentChunk(BaseModel):
    """文档片段"""
    chunk_id: str = Field(description="唯一标识")
    content: str = Field(description="文本内容")
    metadata: ChunkMetadata = Field(description="元数据")
    vector: Optional[List[float]] = Field(default=None, description="向量")

    @classmethod
    def create(cls, content: str, source_file: str, file_type: str, chunk_index: int = 0, **kwargs) -> "DocumentChunk":
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        file_hash = hashlib.md5(source_file.encode()).hexdigest()[:8]
        chunk_id = f"{file_hash}_{chunk_index}_{content_hash}"
        metadata = ChunkMetadata(chunk_id=chunk_id, source_file=source_file, file_type=file_type, chunk_index=chunk_index, **kwargs)
        return cls(chunk_id=chunk_id, content=content, metadata=metadata)


class DocumentElement(BaseModel):
    """文档元素（解析中间结果）"""
    type: str = Field(description="元素类型 (paragraph/table/image/heading)")
    text: Optional[str] = Field(default=None, description="文本内容")
    page_number: Optional[int] = Field(default=None, description="页码")
    line_number: Optional[int] = Field(default=None, description="行号")
    paragraph_index: Optional[int] = Field(default=None, description="段落索引")
    headers: Optional[List[str]] = Field(default=None, description="表格表头")
    rows: Optional[List[List[str]]] = Field(default=None, description="表格数据")
    image_path: Optional[str] = Field(default=None, description="图片路径")
    image_data: Optional[bytes] = Field(default=None, description="图片数据")
    caption: Optional[str] = Field(default=None, description="图片说明")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="额外元数据")


class BaseParserTool(ABC):
    """文件解析工具基类"""

    @property
    @abstractmethod
    def supported_extensions(self) -> List[str]:
        pass

    @abstractmethod
    def validate(self, file_path: str) -> bool:
        pass

    @abstractmethod
    def parse(self, file_path: str) -> List[DocumentChunk]:
        pass

    def parse_with_structure(self, file_path: str) -> List[DocumentElement]:
        chunks = self.parse(file_path)
        return [
            DocumentElement(
                type="paragraph", text=chunk.content,
                page_number=chunk.metadata.page_number,
                line_number=chunk.metadata.line_start,
                paragraph_index=chunk.metadata.paragraph_index
            )
            for chunk in chunks
        ]

    def _generate_chunk_id(self, content: str, source_file: str, index: int) -> str:
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]
        file_hash = hashlib.md5(source_file.encode()).hexdigest()[:8]
        return f"{file_hash}_{index}_{content_hash}"
