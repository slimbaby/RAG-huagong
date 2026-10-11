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


def pytest_collection_modifyitems(config, items):
    """CI 环境自动跳过 @pytest.mark.model 标记的用例
    （这些用例需要下载/加载真实模型权重，CI runner 无 HF 缓存且下载耗时不稳定）"""
    if not os.environ.get("CI"):
        return
    skip_model = pytest.mark.skip(reason="CI 环境跳过真实模型加载用例")
    for item in items:
        if "model" in item.keywords:
            item.add_marker(skip_model)
