"""
SmartTextSplitter 单元测试
"""

import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.tools.text_splitter import SmartTextSplitter
from app.tools.base_tool import DocumentChunk, ChunkMetadata


def _make_text_chunk(content, source_file="test.txt", file_type="txt", chunk_index=0,
                     page_number=None, section_title=None, content_type="text"):
    return DocumentChunk.create(
        content=content, source_file=source_file, file_type=file_type,
        chunk_index=chunk_index, page_number=page_number,
        section_title=section_title, content_type=content_type
    )


def _make_table_chunk(content, source_file="test.xlsx", file_type="xlsx", chunk_index=0):
    return DocumentChunk.create(
        content=content, source_file=source_file, file_type=file_type,
        chunk_index=chunk_index, content_type="table"
    )


def _make_image_chunk(content, source_file="test.pdf", file_type="pdf", chunk_index=0):
    return DocumentChunk.create(
        content=content, source_file=source_file, file_type=file_type,
        chunk_index=chunk_index, content_type="image"
    )


class TestSplitByHeading:
    """heading-aware 切分测试"""

    def test_small_chunk_passthrough(self):
        """小于 chunk_size 的 chunk 直接通过"""
        splitter = SmartTextSplitter(chunk_size=1024)
        chunk = _make_text_chunk("这是一段短文本。")
        result = splitter.split_by_heading([chunk])
        assert len(result) == 1
        assert result[0].content == "这是一段短文本。"

    def test_table_passthrough(self):
        """table 类型 chunk 直接通过，不切分"""
        splitter = SmartTextSplitter(chunk_size=10)
        chunk = _make_table_chunk("| A | B |\n| --- | --- |\n| 1 | 2 |")
        result = splitter.split_by_heading([chunk])
        assert len(result) == 1
        assert result[0].metadata.content_type == "table"

    def test_image_passthrough(self):
        """image 类型 chunk 直接通过"""
        splitter = SmartTextSplitter(chunk_size=10)
        chunk = _make_image_chunk("[图片OCR文字]\n内容")
        result = splitter.split_by_heading([chunk])
        assert len(result) == 1
        assert result[0].metadata.content_type == "image"

    def test_split_by_double_newline(self):
        """按双换行分段"""
        splitter = SmartTextSplitter(chunk_size=10)
        text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        chunk = _make_text_chunk(text)
        result = splitter.split_by_heading([chunk])
        assert len(result) >= 2
        for c in result:
            assert len(c.content.strip()) > 0

    def test_split_preserves_section_title(self):
        """切分后 section_title 被保留"""
        splitter = SmartTextSplitter(chunk_size=50)
        text = "段落一的内容比较长需要切分。\n\n段落二的内容也比较长需要切分。"
        chunk = _make_text_chunk(text, section_title="第一章 总则")
        result = splitter.split_by_heading([chunk])
        for c in result:
            assert c.metadata.section_title == "第一章 总则"

    def test_oversized_paragraph_subsplit(self):
        """超大段落按句子边界子切分"""
        splitter = SmartTextSplitter(chunk_size=20, min_chunk_size=5)
        # 一个没有段落分隔的长文本，每个句子约7个中文字符
        sentences = "这是第一句话内容。这是第二句话内容。这是第三句话内容。这是第四句话内容。这是第五句话内容。这是第六句话内容。这是第七句话内容。这是第八句话内容。这是第九句话内容。这是第十句话内容。"
        chunk = _make_text_chunk(sentences)
        result = splitter.split_by_heading([chunk])
        assert len(result) >= 2

    def test_single_long_paragraph_by_sentence(self):
        """单段超长文本退回句子切分"""
        splitter = SmartTextSplitter(chunk_size=20, min_chunk_size=5)
        text = "这是第一个句子内容。这是第二个句子内容。这是第三个句子内容。这是第四个句子内容。这是第五个句子内容。这是第六个句子内容。"
        chunk = _make_text_chunk(text)
        result = splitter.split_by_heading([chunk])
        assert len(result) >= 3

    def test_empty_content(self):
        """空内容 chunk 直接通过（长度为0 <= chunk_size）"""
        splitter = SmartTextSplitter(chunk_size=1024)
        chunk = _make_text_chunk("")
        result = splitter.split_by_heading([chunk])
        # 空字符串长度为0 <= chunk_size，直接通过
        assert len(result) == 1
        assert result[0].content == ""

    def test_chunk_index_sequential(self):
        """切分后 chunk_index 递增"""
        splitter = SmartTextSplitter(chunk_size=50)
        text = "第一段内容。\n\n第二段内容。\n\n第三段内容。"
        chunk = _make_text_chunk(text, chunk_index=5)
        result = splitter.split_by_heading([chunk])
        indices = [c.metadata.chunk_index for c in result]
        assert indices == list(range(5, 5 + len(result)))

    def test_multiple_chunks_input(self):
        """输入多个 chunk，各自独立切分"""
        splitter = SmartTextSplitter(chunk_size=50)
        chunk1 = _make_text_chunk("短文本。", chunk_index=0)
        chunk2 = _make_text_chunk("第一段。\n\n第二段比较长需要切分处理。", chunk_index=1)
        result = splitter.split_by_heading([chunk1, chunk2])
        assert len(result) >= 2
        # 第一个 chunk 不变
        assert result[0].content == "短文本。"


class TestSplitCompat:
    """兼容旧 split() 接口测试"""

    def test_split_small_passthrough(self):
        splitter = SmartTextSplitter(chunk_size=1024)
        chunk = _make_text_chunk("短文本。")
        result = splitter.split([chunk])
        assert len(result) == 1

    def test_split_table_passthrough(self):
        splitter = SmartTextSplitter(chunk_size=10)
        chunk = _make_table_chunk("大表格内容")
        result = splitter.split([chunk])
        assert len(result) == 1
        assert result[0].metadata.content_type == "table"

    def test_split_by_sentence(self):
        splitter = SmartTextSplitter(chunk_size=20, min_chunk_size=5)
        text = "这是第一个句子内容。这是第二个句子内容。这是第三个句子内容。这是第四个句子内容。这是第五个句子内容。"
        chunk = _make_text_chunk(text)
        result = splitter.split([chunk])
        assert len(result) >= 2


class TestSplitIntoSentences:
    """句子切分测试"""

    def test_chinese_sentences(self):
        splitter = SmartTextSplitter()
        text = "你好。世界！测试？"
        sentences = splitter._split_into_sentences(text)
        assert len(sentences) == 3
        assert sentences[0] == "你好。"
        assert sentences[1] == "世界！"
        assert sentences[2] == "测试？"

    def test_english_sentences(self):
        splitter = SmartTextSplitter()
        text = "Hello. World! Test?"
        sentences = splitter._split_into_sentences(text)
        assert len(sentences) == 3

    def test_newline_split(self):
        splitter = SmartTextSplitter()
        text = "第一行\n第二行\n第三行"
        sentences = splitter._split_into_sentences(text)
        assert len(sentences) == 3

    def test_mixed_content(self):
        splitter = SmartTextSplitter()
        text = "中文句子。English sentence.\n换行内容"
        sentences = splitter._split_into_sentences(text)
        assert len(sentences) == 3


class TestGetOverlapText:
    """overlap 测试"""

    def test_no_overlap(self):
        splitter = SmartTextSplitter(chunk_overlap=0)
        assert splitter._get_overlap_text("hello world") == ""

    def test_overlap_with_sentence_boundary(self):
        splitter = SmartTextSplitter(chunk_overlap=20)
        text = "这是前面的内容。这是overlap区域的文本。"
        overlap = splitter._get_overlap_text(text)
        # 应该从最近的句子边界之后开始
        assert len(overlap) <= 20

    def test_overlap_no_boundary(self):
        splitter = SmartTextSplitter(chunk_overlap=10)
        text = "没有句号分隔的很长的一段文本内容"
        overlap = splitter._get_overlap_text(text)
        assert len(overlap) <= 10


class TestSplitBySection:
    """按 section 分组切分测试"""

    def test_group_by_section(self):
        splitter = SmartTextSplitter(chunk_size=100)
        chunk1 = _make_text_chunk("第一章内容。", section_title="第一章")
        chunk2 = _make_text_chunk("第一章续。", section_title="第一章")
        chunk3 = _make_text_chunk("第二章内容。", section_title="第二章")
        result = splitter.split_by_section([chunk1, chunk2, chunk3])
        sections = {c.metadata.section_title for c in result}
        assert "第一章" in sections
        assert "第二章" in sections


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
