"""
BM25倒排索引 - 支持中文分词
"""

from typing import List, Dict, Tuple, Optional
import logging
import math
import os
import pickle
from collections import defaultdict

logger = logging.getLogger(__name__)


class BM25Index:
    """BM25倒排索引"""

    def __init__(self, k1: float = 1.5, b: float = 0.75, index_path: str = "./bm25_index"):
        self.k1 = k1
        self.b = b
        self.index_path = index_path
        self.inverted_index: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        self.doc_stats: Dict[str, Dict] = {}
        self.doc_count = 0
        self.avg_doc_length = 0
        self._total_length = 0
        self._tokenizer = None
        self._stop_words = self._load_stop_words()

    def _get_tokenizer(self):
        if self._tokenizer is None:
            try:
                import jieba
                self._tokenizer = jieba
            except ImportError:
                self._tokenizer = "regex"
        return self._tokenizer

    def _load_stop_words(self) -> set:
        default_stops = {'的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一', '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有', '看', '好', '自己', '这', '他', '吗', '啊', '呢', '吧', '嗯', '哦', '呃', '额', '然后', '就是', '那个', '这个'}
        return default_stops

    def _tokenize(self, text: str) -> List[str]:
        tokenizer = self._get_tokenizer()
        if tokenizer == "regex":
            import re
            words = re.findall(r'[一-龥a-zA-Z0-9]+', text)
        else:
            words = list(tokenizer.cut(text))
        return [w for w in words if len(w) >= 2 and w not in self._stop_words and not w.isdigit()]

    def add_document(self, doc_id: str, content: str, metadata: Dict = None):
        tokens = self._tokenize(content)
        doc_length = len(tokens)
        # 保留正文片段：向量库不可用时，BM25命中仍能提供可用内容（供reranker/实体兜底/引用）
        self.doc_stats[doc_id] = {
            "length": doc_length,
            "metadata": metadata or {},
            "content": (content or "")[:4000],
        }
        self._total_length += doc_length
        self.doc_count += 1
        self.avg_doc_length = self._total_length / self.doc_count if self.doc_count > 0 else 0

        tf = defaultdict(int)
        for token in tokens:
            tf[token] += 1

        for token, count in tf.items():
            tf_norm = count / doc_length if doc_length > 0 else 0
            self.inverted_index[token].append((doc_id, tf_norm))

    def batch_add(self, documents: List[Dict]):
        for doc in documents:
            self.add_document(doc["doc_id"], doc["content"], doc.get("metadata"))

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        tokens = self._tokenize(query)
        scores: Dict[str, float] = defaultdict(float)

        for token in tokens:
            if token not in self.inverted_index:
                continue

            df = len(self.inverted_index[token])
            idf = math.log((self.doc_count - df + 0.5) / (df + 0.5) + 1)

            for doc_id, tf_norm in self.inverted_index[token]:
                doc_length = self.doc_stats[doc_id]["length"]
                tf_score = (tf_norm * (self.k1 + 1)) / (tf_norm + self.k1 * (1 - self.b + self.b * doc_length / self.avg_doc_length))
                scores[doc_id] += idf * tf_score

        sorted_results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return sorted_results[:top_k]

    def delete_document(self, doc_id: str):
        if doc_id in self.doc_stats:
            doc_length = self.doc_stats[doc_id]["length"]
            self._total_length -= doc_length
            self.doc_count -= 1
            self.avg_doc_length = self._total_length / self.doc_count if self.doc_count > 0 else 0
            del self.doc_stats[doc_id]

        for token in self.inverted_index:
            self.inverted_index[token] = [(did, score) for did, score in self.inverted_index[token] if did != doc_id]

    def save(self):
        os.makedirs(os.path.dirname(self.index_path) if os.path.dirname(self.index_path) else ".", exist_ok=True)
        tmp_path = self.index_path + ".pickle.tmp"
        with open(tmp_path, "wb") as f:
            pickle.dump({
                "inverted_index": dict(self.inverted_index),
                "doc_stats": self.doc_stats,
                "doc_count": self.doc_count,
                "avg_doc_length": self.avg_doc_length,
                "total_length": self._total_length,
            }, f)
        os.replace(tmp_path, self.index_path + ".pickle")

    @classmethod
    def load(cls, index_path: str) -> "BM25Index":
        instance = cls(index_path=index_path)
        pickle_path = index_path + ".pickle"
        if os.path.exists(pickle_path):
            with open(pickle_path, "rb") as f:
                data = pickle.load(f)
            instance.inverted_index = defaultdict(list, data["inverted_index"])
            instance.doc_stats = data["doc_stats"]
            instance.doc_count = data["doc_count"]
            instance.avg_doc_length = data["avg_doc_length"]
            instance._total_length = data["total_length"]
        return instance

    def get_stats(self) -> Dict:
        return {
            "doc_count": self.doc_count,
            "avg_doc_length": self.avg_doc_length,
            "vocabulary_size": len(self.inverted_index),
            "total_terms": self._total_length,
        }
