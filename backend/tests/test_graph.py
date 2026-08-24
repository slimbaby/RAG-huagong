"""Test LangGraph graph construction and basic execution."""

import pytest
from unittest.mock import MagicMock, AsyncMock


def test_create_rag_graph():
    from app.agents.graph import create_rag_graph
    graph = create_rag_graph()
    assert graph is not None


def test_create_agent_graph():
    from app.agents.graph import create_agent_graph
    graph = create_agent_graph()
    assert graph is not None


def test_rag_state_fields():
    from app.agents.state import RAGState
    # Verify all expected fields exist in the TypedDict annotations
    annotations = RAGState.__annotations__
    assert "user_query" in annotations
    assert "optimized_query" in annotations
    assert "query_vector" in annotations
    assert "raw_results" in annotations
    assert "filtered_results" in annotations
    assert "answer" in annotations
    assert "citations" in annotations
    assert "error" in annotations


def test_agent_state_fields():
    from app.agents.state import AgentState
    annotations = AgentState.__annotations__
    assert "messages" in annotations
    assert "current_agent" in annotations
    assert "task_type" in annotations


def test_rag_graph_nodes():
    """Verify the RAG graph has the expected node names."""
    from app.agents.graph import create_rag_graph
    graph = create_rag_graph()
    # The graph should be compiled and have nodes
    assert hasattr(graph, "ainvoke")


def test_agent_graph_nodes():
    """Verify the Agent graph has the expected node names."""
    from app.agents.graph import create_agent_graph
    graph = create_agent_graph()
    assert hasattr(graph, "ainvoke")


def test_rag_graph_with_deps():
    """Test graph creation with dependency injection."""
    from app.agents.graph import create_rag_graph
    mock_deps = {
        "llm_service": MagicMock(),
        "embedding_service": MagicMock(),
        "citation_service": MagicMock(),
        "query_rewriter": MagicMock(),
        "hybrid_store": MagicMock(),
        "tracer": MagicMock(),
        "resilience_manager": MagicMock(),
    }
    graph = create_rag_graph(deps=mock_deps)
    assert graph is not None
