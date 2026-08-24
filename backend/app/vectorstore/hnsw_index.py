"""
HNSW索引配置
"""

from dataclasses import dataclass


@dataclass
class HNSWConfig:
    M: int = 32
    ef_construction: int = 256
    ef_search: int = 128
    max_elements: int = 1000000
    metric_type: str = "COSINE"

    def to_milvus_params(self) -> dict:
        return {"M": self.M, "efConstruction": self.ef_construction}

    def to_search_params(self) -> dict:
        return {"ef": self.ef_search}

    @classmethod
    def from_config(cls, config) -> "HNSWConfig":
        return cls(
            M=getattr(config, "HNSW_M", 32),
            ef_construction=getattr(config, "HNSW_EF_CONSTRUCTION", 256),
            ef_search=getattr(config, "HNSW_EF_SEARCH", 128),
            metric_type=getattr(config, "HNSW_METRIC_TYPE", "COSINE"),
        )
