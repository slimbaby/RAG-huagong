"""
评估数据集管理器
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import json
import os
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class EvalDataset:
    dataset_id: str
    name: str
    description: str
    samples: List[Dict]
    created_at: str
    updated_at: str
    version: int = 1
    tags: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class EvalDatasetManager:
    """评估数据集管理器"""

    def __init__(self, storage_dir: str = "./evaluation"):
        self.storage_dir = storage_dir
        self.index_path = os.path.join(storage_dir, "datasets_index.json")
        self._index = self._load_index()
        os.makedirs(storage_dir, exist_ok=True)

    def _load_index(self) -> Dict:
        if os.path.exists(self.index_path):
            with open(self.index_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"datasets": {}}

    def _save_index(self):
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self._index, f, ensure_ascii=False, indent=2)

    def create_dataset(self, name: str, description: str, samples: List[Dict], tags: List[str] = None, metadata: Dict = None) -> EvalDataset:
        dataset_id = str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        self._validate_samples(samples)
        dataset = EvalDataset(dataset_id=dataset_id, name=name, description=description, samples=samples, created_at=now, updated_at=now, tags=tags or [], metadata=metadata or {})
        self._index["datasets"][dataset_id] = {"name": name, "description": description, "sample_count": len(samples), "created_at": now}
        self._save_index()
        self._save_dataset(dataset)
        return dataset

    def get_dataset(self, dataset_id: str) -> Optional[EvalDataset]:
        file_path = os.path.join(self.storage_dir, f"dataset_{dataset_id}.json")
        if os.path.exists(file_path):
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return EvalDataset(**data)
        return None

    def list_datasets(self, tags: List[str] = None) -> List[Dict]:
        datasets = list(self._index["datasets"].values())
        return datasets

    def update_dataset(self, dataset_id: str, name: str = None, description: str = None, tags: List[str] = None):
        dataset = self.get_dataset(dataset_id)
        if not dataset:
            return
        if name:
            dataset.name = name
        if description:
            dataset.description = description
        if tags:
            dataset.tags = tags
        dataset.updated_at = datetime.now().isoformat()
        dataset.version += 1
        self._save_dataset(dataset)

    def delete_dataset(self, dataset_id: str):
        file_path = os.path.join(self.storage_dir, f"dataset_{dataset_id}.json")
        if os.path.exists(file_path):
            os.remove(file_path)
        self._index["datasets"].pop(dataset_id, None)
        self._save_index()

    def add_samples(self, dataset_id: str, samples: List[Dict]):
        dataset = self.get_dataset(dataset_id)
        if not dataset:
            return
        self._validate_samples(samples)
        dataset.samples.extend(samples)
        dataset.updated_at = datetime.now().isoformat()
        dataset.version += 1
        self._save_dataset(dataset)

    def export_dataset(self, dataset_id: str, format: str = "json") -> str:
        dataset = self.get_dataset(dataset_id)
        if not dataset:
            return ""
        if format == "json":
            return json.dumps(dataset.__dict__, ensure_ascii=False, indent=2)
        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            if dataset.samples:
                writer = csv.DictWriter(output, fieldnames=dataset.samples[0].keys())
                writer.writeheader()
                writer.writerows(dataset.samples)
            return output.getvalue()
        return ""

    def import_dataset(self, data: str, format: str = "json", name: str = "", description: str = "") -> EvalDataset:
        if format == "json":
            samples = json.loads(data)
            if isinstance(samples, dict):
                samples = samples.get("samples", [])
        elif format == "csv":
            import csv
            import io
            reader = csv.DictReader(io.StringIO(data))
            samples = list(reader)
        else:
            samples = []
        return self.create_dataset(name=name, description=description, samples=samples)

    def _validate_samples(self, samples: List[Dict]):
        for s in samples:
            if "question" not in s or "answer" not in s:
                raise ValueError("Each sample must have 'question' and 'answer' fields")
            if "contexts" in s and not isinstance(s["contexts"], list):
                raise ValueError("'contexts' must be a list")

    def _save_dataset(self, dataset: EvalDataset):
        file_path = os.path.join(self.storage_dir, f"dataset_{dataset.dataset_id}.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(dataset.__dict__, f, ensure_ascii=False, indent=2)
