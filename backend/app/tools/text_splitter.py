"""
智能文本分割器 - 按语义切分，而非固定长度
"""

from typing import List, Optional
from .base_tool import DocumentChunk
import re


class SmartTextSplitter:
    """智能文本分割器 - 保持语义完整性"""

    def __init__(self, chunk_size: int = 1024, chunk_overlap: int = 100, min_chunk_size: int = 100, separators: Optional[List[str]] = None):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size
        self.separators = separators or ["\n\n", "\n", "。", "！", "？", ".", "!", "?", "；", ";", "，", ","]

    def split(self, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
        """兼容旧接口：对超大 text chunk 按句子边界切分"""
        result = []
        for chunk in chunks:
            if chunk.metadata.content_type in ("table", "image"):
                result.append(chunk)
            elif len(chunk.content) <= self.chunk_size:
                result.append(chunk)
            else:
                result.extend(self._split_text(chunk))
        return result

    def split_by_heading(self, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
        """heading-aware 切分：对 text 类型 chunk 按段落结构重切

        优先按段落边界（\\n\\n 或 \\n）切分，保留完整段落；
        超大段落再按句子边界子切分。
        """
        result = []
        for chunk in chunks:
            if chunk.metadata.content_type in ("table", "image"):
                result.append(chunk)
            elif len(chunk.content) <= self.chunk_size:
                result.append(chunk)
            else:
                result.extend(self._split_by_paragraph(chunk))
        return result

    def _split_by_paragraph(self, chunk: DocumentChunk) -> List[DocumentChunk]:
        """按段落边界切分，超大段落按句子子切分"""
        text = chunk.content
        metadata = chunk.metadata

        # 先按双换行分段
        paragraphs = re.split(r'\n\n+', text)
        if len(paragraphs) <= 1:
            # 没有双换行分段，尝试按单换行
            paragraphs = re.split(r'\n', text)
        if len(paragraphs) <= 1:
            # 仍然只有一段，退回句子切分
            return self._split_text(chunk)

        result_chunks = []
        current = ""
        chunk_index = metadata.chunk_index

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_with_sep = para + "\n\n"

            # 单段落本身就超大 → 子切分
            if len(para) > self.chunk_size:
                # 先 flush 当前累积
                if current.strip():
                    result_chunks.append(self._make_chunk(current.strip(), metadata, chunk_index))
                    chunk_index += 1
                    current = ""
                # 对超大段落按句子切分
                sub_chunk = DocumentChunk.create(
                    content=para, source_file=metadata.source_file,
                    file_type=metadata.file_type, chunk_index=chunk_index,
                    page_number=metadata.page_number, line_start=metadata.line_start,
                    line_end=metadata.line_end, section_title=metadata.section_title,
                    content_type=metadata.content_type
                )
                result_chunks.extend(self._split_text(sub_chunk))
                chunk_index = result_chunks[-1].metadata.chunk_index + 1 if result_chunks else chunk_index
                continue

            if len(current) + len(para_with_sep) > self.chunk_size and current:
                result_chunks.append(self._make_chunk(current.strip(), metadata, chunk_index))
                chunk_index += 1
                overlap = self._get_overlap_text(current)
                current = overlap + para_with_sep
            else:
                current += para_with_sep

        if current.strip():
            result_chunks.append(self._make_chunk(current.strip(), metadata, chunk_index))
        return result_chunks

    def _split_text(self, chunk: DocumentChunk) -> List[DocumentChunk]:
        """按句子边界切分（原有逻辑）"""
        text = chunk.content
        metadata = chunk.metadata
        sentences = self._split_into_sentences(text)
        current_chunk = ""
        current_length = 0
        result_chunks = []
        chunk_index = metadata.chunk_index

        for sentence in sentences:
            sentence_len = len(sentence)
            if current_length + sentence_len > self.chunk_size and current_chunk:
                if len(current_chunk.strip()) >= self.min_chunk_size:
                    result_chunks.append(self._make_chunk(current_chunk.strip(), metadata, chunk_index))
                    chunk_index += 1
                overlap_text = self._get_overlap_text(current_chunk)
                current_chunk = overlap_text + sentence
                current_length = len(current_chunk)
            else:
                current_chunk += sentence
                current_length += sentence_len

        if current_chunk.strip() and len(current_chunk.strip()) >= self.min_chunk_size:
            result_chunks.append(self._make_chunk(current_chunk.strip(), metadata, chunk_index))
        return result_chunks

    def _make_chunk(self, content: str, metadata, chunk_index: int) -> DocumentChunk:
        """创建新 chunk，继承原始 metadata"""
        return DocumentChunk.create(
            content=content, source_file=metadata.source_file,
            file_type=metadata.file_type, chunk_index=chunk_index,
            page_number=metadata.page_number, line_start=metadata.line_start,
            line_end=metadata.line_end, section_title=metadata.section_title,
            content_type=metadata.content_type
        )

    def _split_into_sentences(self, text: str) -> List[str]:
        sentences = []
        current = ""
        for char in text:
            current += char
            if char in "。！？.!?\n":
                if current.strip():
                    sentences.append(current)
                current = ""
        if current.strip():
            sentences.append(current)
        return sentences

    def _get_overlap_text(self, text: str) -> str:
        if self.chunk_overlap <= 0:
            return ""
        overlap = text[-self.chunk_overlap:]
        for sep in ["。", "！", "？", ".", "!", "?", "\n", "；", ";"]:
            idx = overlap.rfind(sep)
            if idx > 0:
                return overlap[idx + 1:]
        return overlap

    def split_by_section(self, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
        result = []
        sections = {}
        for chunk in chunks:
            section = chunk.metadata.section_title or "default"
            if section not in sections:
                sections[section] = []
            sections[section].append(chunk)

        for section_name, section_chunks in sections.items():
            combined_text = "\n".join([c.content for c in section_chunks])
            if len(combined_text) <= self.chunk_size:
                result.extend(section_chunks)
            else:
                first_chunk = section_chunks[0]
                combined_chunk = DocumentChunk.create(
                    content=combined_text, source_file=first_chunk.metadata.source_file,
                    file_type=first_chunk.metadata.file_type, chunk_index=0,
                    page_number=first_chunk.metadata.page_number, section_title=section_name,
                    content_type=first_chunk.metadata.content_type
                )
                split_chunks = self._split_text(combined_chunk)
                result.extend(split_chunks)
        return result
