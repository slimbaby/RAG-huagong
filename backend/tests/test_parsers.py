"""Test document parsers."""

import os
import pytest
import tempfile
import json


def test_text_parser():
    from app.tools.text_parser import TextParserTool
    parser = TextParserTool()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("第一行内容\n第二行内容\n第三行内容")
        temp_path = f.name

    try:
        assert parser.validate(temp_path)
        chunks = parser.parse(temp_path)
        assert len(chunks) > 0
        assert "第一行内容" in chunks[0].content
    finally:
        os.unlink(temp_path)


def test_markdown_parser():
    from app.tools.text_parser import TextParserTool
    parser = TextParserTool()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# 标题一\n正文内容1\n## 标题二\n正文内容2")
        temp_path = f.name

    try:
        assert parser.validate(temp_path)
        chunks = parser.parse(temp_path)
        assert len(chunks) >= 2
    finally:
        os.unlink(temp_path)


def test_json_parser():
    from app.tools.json_parser import JSONParserTool
    parser = JSONParserTool()

    data = {"name": "测试", "value": 123, "nested": {"key": "val"}}
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        temp_path = f.name

    try:
        assert parser.validate(temp_path)
        chunks = parser.parse(temp_path)
        assert len(chunks) > 0
    finally:
        os.unlink(temp_path)


def test_csv_parser():
    from app.tools.csv_parser import CSVParserTool
    parser = CSVParserTool()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write("name,value,category\n项目A,100,类别1\n项目B,200,类别2\n")
        temp_path = f.name

    try:
        assert parser.validate(temp_path)
        chunks = parser.parse(temp_path)
        assert len(chunks) > 0
        assert "项目A" in chunks[0].content
    finally:
        os.unlink(temp_path)


def test_text_splitter():
    from app.tools.text_splitter import SmartTextSplitter
    from app.tools.base_tool import DocumentChunk
    splitter = SmartTextSplitter(chunk_size=200, chunk_overlap=50, min_chunk_size=50)

    # Create a long chunk that exceeds chunk_size
    long_text = "这是一段较长的测试文本，用于验证智能分块功能是否正常工作。" * 20
    chunk = DocumentChunk.create(
        content=long_text,
        source_file="test.txt",
        file_type="txt",
        chunk_index=0
    )
    result = splitter.split([chunk])
    assert len(result) >= 1
    # All content should be preserved
    combined = "".join(c.content for c in result)
    assert "测试文本" in combined


def test_document_chunk_create():
    from app.tools.base_tool import DocumentChunk
    chunk = DocumentChunk.create(
        content="test content",
        source_file="test.pdf",
        file_type="pdf",
        chunk_index=0,
        section_title="第一章",
        content_type="text"
    )
    assert chunk.content == "test content"
    assert chunk.metadata.source_file == "test.pdf"
    assert chunk.metadata.file_type == "pdf"
    assert chunk.metadata.section_title == "第一章"


def test_get_parser():
    from app.tools import get_parser

    assert get_parser("test.pdf") is not None
    assert get_parser("test.docx") is not None
    assert get_parser("test.xlsx") is not None
    assert get_parser("test.xls") is not None
    assert get_parser("test.csv") is not None
    assert get_parser("test.json") is not None
    assert get_parser("test.txt") is not None
    assert get_parser("test.md") is not None
    # Unknown extensions raise ValueError
    with pytest.raises(ValueError):
        get_parser("test.unknown")
