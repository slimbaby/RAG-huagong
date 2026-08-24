"""
文档版本管理 - 追踪法规变更
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import hashlib
import json
import os
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class DocumentVersion:
    version_id: str
    document_id: str
    content_hash: str
    title: str
    url: str
    publish_date: str = ""
    effective_date: str = ""
    source: str = ""
    category: str = ""
    content: str = ""
    created_at: str = ""
    is_current: bool = True
    change_summary: str = ""


class VersionManager:
    """文档版本管理器"""

    def __init__(self, storage_dir: str = "./law_archives"):
        self.storage_dir = storage_dir
        self.index_path = os.path.join(storage_dir, "version_index.json")
        self._index = self._load_index()
        os.makedirs(storage_dir, exist_ok=True)

    def _load_index(self) -> Dict:
        if os.path.exists(self.index_path):
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"documents": {}, "versions": {}, "current": {}, "hash_index": {}}

    def _save_index(self):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def add_version(self, url: str, title: str, content: str, content_hash: str, publish_date: str = "", effective_date: str = "", source: str = "", category: str = "") -> Optional[DocumentVersion]:
        if content_hash in self._index["hash_index"]:
            return None

        if url not in self._index["documents"]:
            doc_id = str(uuid.uuid4())[:8]
            self._index["documents"][url] = doc_id
            self._index["versions"][doc_id] = []
        else:
            doc_id = self._index["documents"][url]
            current_version_id = self._index["current"].get(doc_id)
            if current_version_id:
                for v in self._index["versions"][doc_id]:
                    if v.get("version_id") == current_version_id:
                        v["is_current"] = False

        version_id = str(uuid.uuid4())[:8]
        change_summary = self._generate_change_summary(doc_id, content)

        version = DocumentVersion(
            version_id=version_id, document_id=doc_id, content_hash=content_hash,
            title=title, url=url, publish_date=publish_date, effective_date=effective_date,
            source=source, category=category, content=content[:5000],
            created_at=datetime.now().isoformat(), is_current=True, change_summary=change_summary,
        )

        self._index["versions"][doc_id].append(version.__dict__)
        self._index["current"][doc_id] = version_id
        self._index["hash_index"][content_hash] = version_id
        self._save_index()

        version_file = os.path.join(self.storage_dir, f"version_{version_id}.json")
        with open(version_file, "w", encoding="utf-8") as f:
            json.dump(version.__dict__, f, ensure_ascii=False, indent=2)

        return version

    def get_current_version(self, document_id: str) -> Optional[Dict]:
        version_id = self._index["current"].get(document_id)
        if version_id:
            return self.get_version(version_id)
        return None

    def get_version(self, version_id: str) -> Optional[Dict]:
        version_file = os.path.join(self.storage_dir, f"version_{version_id}.json")
        if os.path.exists(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def get_all_versions(self, document_id: str) -> List[Dict]:
        return self._index["versions"].get(document_id, [])

    def get_document_by_url(self, url: str) -> Optional[str]:
        return self._index["documents"].get(url)

    def list_documents(self, category: str = None) -> List[Dict]:
        docs = []
        for url, doc_id in self._index["documents"].items():
            current = self.get_current_version(doc_id)
            if current:
                if category and current.get("category") != category:
                    continue
                docs.append(current)
        return docs

    def get_version_history(self, url: str) -> List[Dict]:
        doc_id = self._index["documents"].get(url)
        if doc_id:
            return self.get_all_versions(doc_id)
        return []

    def _generate_change_summary(self, document_id: str, new_content: str) -> str:
        current = self.get_current_version(document_id)
        if not current:
            return "首次收录"
        old_len = len(current.get("content", ""))
        new_len = len(new_content)
        diff = new_len - old_len
        if diff > 0:
            return f"内容增加约{diff}字符"
        elif diff < 0:
            return f"内容减少约{abs(diff)}字符"
        return "内容更新"
