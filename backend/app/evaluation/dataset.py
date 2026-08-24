"""
评测集数据结构 - 支持多轮场景和反幻觉断言
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
import json
import os
import logging

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """单条评测用例"""
    id: str                                    # 唯一标识
    question: str                              # 评测问题
    category: str                              # "单文档查询" / "多轮指代" / "反幻觉" / "跨文档推理" / "通用知识"
    difficulty: str = "medium"                 # "easy" / "medium" / "hard"
    ground_truth: str = ""                     # 期望答案（RAGAS 用）
    expected_contains: List[str] = field(default_factory=list)    # 答案中必须包含的关键词（任一匹配）
    expected_not_contains: List[str] = field(default_factory=list)  # 答案中不能出现的内容（反幻觉）
    expected_source: str = ""                  # 期望引用的来源文件名
    setup_questions: List[str] = field(default_factory=list)      # 前置多轮问题
    depends_on_session: bool = False           # 是否依赖多轮会话
    tags: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)
    source_doc_id: str = ""                    # 关联的文档 ID（文档用例专用）
    layer: str = "core"                        # "core" / "document" / "regression"
    stale: bool = False                        # 文档变更后标记为失效

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "question": self.question,
            "category": self.category,
            "difficulty": self.difficulty,
            "ground_truth": self.ground_truth,
            "expected_contains": self.expected_contains,
            "expected_not_contains": self.expected_not_contains,
            "expected_source": self.expected_source,
            "setup_questions": self.setup_questions,
            "depends_on_session": self.depends_on_session,
            "tags": self.tags,
            "metadata": self.metadata,
            "source_doc_id": self.source_doc_id,
            "layer": self.layer,
            "stale": self.stale,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "EvalCase":
        return cls(
            id=data.get("id", ""),
            question=data.get("question", ""),
            category=data.get("category", "单文档查询"),
            difficulty=data.get("difficulty", "medium"),
            ground_truth=data.get("ground_truth", ""),
            expected_contains=data.get("expected_contains", []),
            expected_not_contains=data.get("expected_not_contains", []),
            expected_source=data.get("expected_source", ""),
            setup_questions=data.get("setup_questions", []),
            depends_on_session=data.get("depends_on_session", False),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
            source_doc_id=data.get("source_doc_id", ""),
            layer=data.get("layer", "core"),
            stale=data.get("stale", False),
        )


def load_dataset(path: str) -> List[EvalCase]:
    """从 JSON 文件加载评测集"""
    if not os.path.exists(path):
        logger.error(f"评测集不存在: {path}")
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict):
        data = data.get("cases", data.get("samples", []))

    cases = []
    for item in data:
        try:
            cases.append(EvalCase.from_dict(item))
        except Exception as e:
            logger.warning(f"跳过无效评测用例: {e}")

    logger.info(f"加载评测集: {len(cases)} 条用例 (from {path})")
    return cases


def save_dataset(cases: List[EvalCase], path: str):
    """保存评测集到 JSON 文件"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    data = [c.to_dict() for c in cases]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"评测集已保存: {len(cases)} 条用例 (to {path})")


def mark_stale_by_doc(cases: List[EvalCase], doc_id: str) -> int:
    """标记指定文档关联的用例为 stale，返回标记数量"""
    count = 0
    for case in cases:
        if case.source_doc_id == doc_id and not case.stale:
            case.stale = True
            count += 1
    if count:
        logger.info(f"已标记 {count} 条用例为失效 (doc_id={doc_id})")
    return count


def remove_stale(cases: List[EvalCase]) -> List[EvalCase]:
    """过滤掉 stale 用例"""
    return [c for c in cases if not c.stale]


# ── PostgreSQL 异步操作 ──────────────────────────────────────────

async def load_cases_from_db() -> List[EvalCase]:
    """从 PostgreSQL 加载评测集"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT data FROM eval_cases ORDER BY created_at")
    return [EvalCase.from_dict(json.loads(r['data'])) for r in rows]


async def save_cases_to_db(cases: List[EvalCase]):
    """保存评测集到 PostgreSQL（全量替换）"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM eval_cases")
            for c in cases:
                await conn.execute(
                    """INSERT INTO eval_cases (id, data, layer, source_doc_id, stale)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (id) DO UPDATE SET
                       data=$2, layer=$3, source_doc_id=$4, stale=$5, updated_at=NOW()""",
                    c.id, json.dumps(c.to_dict(), ensure_ascii=False),
                    c.layer, c.source_doc_id, c.stale
                )


async def add_cases_to_db(cases: List[EvalCase]):
    """追加评测用例到 PostgreSQL（不删除已有数据）"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for c in cases:
                await conn.execute(
                    """INSERT INTO eval_cases (id, data, layer, source_doc_id, stale)
                       VALUES ($1, $2, $3, $4, $5)
                       ON CONFLICT (id) DO UPDATE SET
                       data=$2, layer=$3, source_doc_id=$4, stale=$5, updated_at=NOW()""",
                    c.id, json.dumps(c.to_dict(), ensure_ascii=False),
                    c.layer, c.source_doc_id, c.stale
                )


async def mark_stale_by_doc_db(doc_id: str) -> int:
    """在 PostgreSQL 中标记指定文档的用例为 stale"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE eval_cases SET stale=TRUE, updated_at=NOW() WHERE source_doc_id=$1 AND stale=FALSE",
            doc_id
        )
    # asyncpg 返回 "UPDATE N"
    return int(result.split()[-1])
