"""
RAGFlow客户端 - 对接RAGFlow文档解析服务
"""

import httpx
import logging
import asyncio
from typing import Dict, List, Optional

from ..core.config import settings

logger = logging.getLogger(__name__)


class RAGFlowClient:
    """RAGFlow REST API客户端"""

    def __init__(self):
        self.base_url = f"http://{settings.RAGFLOW_HOST}:{settings.RAGFLOW_PORT}"
        self.api_key = settings.RAGFLOW_API_KEY
        self.dataset_name = settings.RAGFLOW_DATASET_NAME
        self.poll_interval = settings.RAGFLOW_POLL_INTERVAL
        self.poll_timeout = settings.RAGFLOW_POLL_TIMEOUT
        self._dataset_id = None

    async def _get_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=60
        )

    async def ensure_dataset(self) -> str:
        if self._dataset_id:
            return self._dataset_id
        async with await self._get_client() as client:
            resp = await client.get("/api/v1/datasets")
            if resp.status_code == 200:
                data = resp.json()
                datasets = data.get("data", [])
                for ds in datasets:
                    if ds.get("name") == self.dataset_name:
                        self._dataset_id = ds["id"]
                        return self._dataset_id
            resp = await client.post("/api/v1/datasets", json={"name": self.dataset_name})
            if resp.status_code == 200:
                self._dataset_id = resp.json()["data"]["id"]
                return self._dataset_id
        raise RuntimeError("Failed to ensure RAGFlow dataset")

    async def upload_and_parse(self, file_path: str, file_name: str) -> List[Dict]:
        dataset_id = await self.ensure_dataset()
        async with await self._get_client() as client:
            with open(file_path, "rb") as f:
                resp = await client.post(
                    f"/api/v1/datasets/{dataset_id}/documents",
                    files={"file": (file_name, f, "application/octet-stream")}
                )
            if resp.status_code != 200:
                raise RuntimeError(f"Upload failed: {resp.text}")
            doc_id = resp.json()["data"][0]["id"]
            await client.post(f"/api/v1/datasets/{dataset_id}/documents/{doc_id}/run")
            await self._poll_document_status(client, dataset_id, doc_id)
            resp = await client.get(f"/api/v1/datasets/{dataset_id}/documents/{doc_id}/chunks")
            if resp.status_code == 200:
                return resp.json().get("data", [])
        return []

    async def _poll_document_status(self, client: httpx.AsyncClient, dataset_id: str, doc_id: str):
        elapsed = 0
        while elapsed < self.poll_timeout:
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval
            resp = await client.get(f"/api/v1/datasets/{dataset_id}/documents/{doc_id}")
            if resp.status_code == 200:
                status = resp.json().get("data", {}).get("status", "")
                if status in ["done", "failed", "complete"]:
                    return
