"""Pytest configuration and fixtures."""

import os
import sys
import pytest

# Add the backend directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def setup_test_env(monkeypatch):
    """Set up test environment variables."""
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("APP_DEBUG", "true")
    monkeypatch.setenv("PREMIUM_MODEL_API_KEY", "test_key")
    monkeypatch.setenv("STANDARD_MODEL_API_KEY", "test_key")
    monkeypatch.setenv("RAGFLOW_ENABLED", "false")
    monkeypatch.setenv("CRAWLER_ENABLED", "false")
    monkeypatch.setenv("UPLOAD_DIR", "./test_uploads")
    monkeypatch.setenv("BM25_INDEX_PATH", "./test_bm25_index")
