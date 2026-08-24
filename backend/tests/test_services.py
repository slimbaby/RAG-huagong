"""Test services module."""

import pytest
from unittest.mock import MagicMock


def test_citation_service():
    from app.services.citation_service import CitationService
    service = CitationService()

    answer = "根据文档，化工产品需要特殊储存[来源1: 化工手册.pdf, 第3页]。"
    source_chunks = [{"source_file": "化工手册.pdf", "page_number": 3}]
    result = service.parse_citations(answer, source_chunks)
    assert isinstance(result, dict)
    assert "citations" in result
    assert "answer" in result


def test_citation_service_no_citations():
    from app.services.citation_service import CitationService
    service = CitationService()

    answer = "这是一段没有引用的回答。"
    result = service.parse_citations(answer, [])
    assert isinstance(result, dict)
    assert result.get("citation_count", 0) == 0


def test_query_rewriter_init():
    from app.services.query_rewriter import QueryRewriter
    mock_llm = MagicMock()
    rewriter = QueryRewriter(llm_client=mock_llm)
    assert rewriter.llm == mock_llm


def test_llm_service_init():
    from app.services.llm_service import LLMService
    service = LLMService(
        api_key="test_key",
        base_url="https://api.example.com/v1",
        model_name="test-model"
    )
    assert service.model_name == "test-model"


def test_embedding_service_init():
    import importlib
    if importlib.util.find_spec("sentence_transformers") is None:
        pytest.skip("sentence_transformers not installed")
    from app.services.embedding_service import EmbeddingService
    from app.core.model_config import EmbeddingConfig
    config = EmbeddingConfig(
        provider="local",
        model_name="BAAI/bge-base-zh-v1.5",
        api_key="",
        api_base_url="",
        dimension=768
    )
    service = EmbeddingService(config=config)
    assert service is not None


def test_ragflow_client_init():
    from app.services.ragflow_client import RAGFlowClient
    client = RAGFlowClient()
    assert client is not None
