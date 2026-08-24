"""Test that all modules can be imported correctly."""

import pytest


def test_import_core():
    from app.core import settings, ModelManager, ResilienceConfig
    assert settings is not None
    assert ModelManager is not None
    assert ResilienceConfig is not None


def test_import_config():
    from app.core.config import Settings
    s = Settings()
    assert "RAG" in s.APP_NAME


def test_import_model_config():
    from app.core.model_config import ModelProvider, ModelLevel, ModelManager
    assert ModelProvider.XIAOMI == "xiaomi"
    assert ModelLevel.PREMIUM == "premium"


def test_import_tools():
    from app.tools import get_parser
    parser = get_parser("test.pdf")
    assert parser is not None
    parser = get_parser("test.docx")
    assert parser is not None
    parser = get_parser("test.xlsx")
    assert parser is not None
    parser = get_parser("test.csv")
    assert parser is not None
    parser = get_parser("test.json")
    assert parser is not None
    parser = get_parser("test.txt")
    assert parser is not None
    parser = get_parser("test.md")
    assert parser is not None


def test_import_base_tool():
    from app.tools.base_tool import DocumentChunk, DocumentElement, ChunkMetadata
    chunk = DocumentChunk.create(
        content="test content",
        source_file="test.pdf",
        file_type="pdf",
        chunk_index=0
    )
    assert chunk.content == "test content"
    assert chunk.metadata.source_file == "test.pdf"
    assert chunk.metadata.file_type == "pdf"


def test_import_services():
    from app.services import LLMService, EmbeddingService, CitationService, QueryRewriter
    assert LLMService is not None
    assert EmbeddingService is not None
    assert CitationService is not None
    assert QueryRewriter is not None


def test_import_vectorstore():
    from app.vectorstore import HybridMilvusStore, BM25Index, HNSWConfig
    assert HybridMilvusStore is not None
    assert BM25Index is not None
    assert HNSWConfig is not None


def test_import_resilience():
    from app.resilience import CircuitBreaker, TimeoutController, ResilienceManager
    assert CircuitBreaker is not None
    assert TimeoutController is not None
    assert ResilienceManager is not None


def test_import_tracing():
    from app.tracing import Tracer
    assert Tracer is not None


def test_import_agents():
    from app.agents import AgentState, RAGState, create_rag_graph, create_agent_graph
    assert AgentState is not None
    assert RAGState is not None
    assert create_rag_graph is not None
    assert create_agent_graph is not None


def test_import_api_routes():
    from app.api import chat, documents, health, resilience, errors, tracing, checkpoints
    assert chat.router is not None
    assert documents.router is not None
    assert health.router is not None


def test_import_main():
    from app.main import app
    assert app is not None
    assert "RAG" in app.title
    assert app.version == "2.0.0"
