"""
反思教训存储 - lesson 的写入、召回、注入

与 MemoryManager 解耦，独立生命周期：
  - 记忆（Memory）：跟 session 走，30 天过期，存"用户是谁/决定过什么"
  - 教训（Lesson）：分两级，存"这类问题应该怎么做"

两类 lesson：
  - 系统级 (scope="__system__")：来自离线评测归因（落点4），每次请求无条件注入
  - 会话级 (scope=真实session_id)：来自在线自评失败（落点3），按会话召回注入

为什么独立成 LessonStore 而不是塞进 MemoryManager：
  1. 生命周期不同——系统级教训跟评测集绑定，不该跟 session 一起过期
  2. 机制不同——教训需要置信度门槛、来源标记、上限淘汰，记忆没有
  3. 语义位不同——教训是"行为准则"，记忆是"事实/背景"，混在一起会让 LLM
     把教训当依据引用（参见 prompts/memory.py 中 lessons 独立段落的约束）
"""

import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 系统级 scope 标识
SYSTEM_SCOPE = "__system__"


@dataclass
class Lesson:
    """一条教训"""
    lesson_id: str
    scope: str                 # "__system__" 或 session_id
    content: str               # 教训正文（可独立理解的陈述句）
    dimension: str = "通用"     # 召回/忠实/完整/引用/理解
    action: str = ""           # 指向的改进动作
    importance: float = 0.5    # 系统级 0.8+，会话级 0.4-0.5
    confidence: float = 0.7    # 置信度 0-1，低于门槛不写入
    source: str = "manual"     # eval_attribution / self_critique / manual
    created_at: float = 0.0
    access_count: int = 0
    last_accessed: float = 0.0
    fingerprint: str = ""


def _fingerprint(content: str) -> str:
    return hashlib.md5(content.encode()).hexdigest()[:16]


class LessonStore:
    """教训存储"""

    def __init__(self, config=None, llm_client=None):
        self.llm = llm_client
        self.storage_path = getattr(config, "LESSON_STORAGE_PATH", "./lessons") if config else "./lessons"
        self.lessons_file = os.path.join(self.storage_path, "lessons.jsonl")
        os.makedirs(self.storage_path, exist_ok=True)

        self.max_system = getattr(config, "LESSON_MAX_SYSTEM", 10) if config else 10
        self.max_session = getattr(config, "LESSON_MAX_SESSION", 5) if config else 5
        self.min_confidence = getattr(config, "LESSON_MIN_CONFIDENCE", 0.5) if config else 0.5
        self.inject_enabled = getattr(config, "LESSON_INJECTION_ENABLED", True) if config else True
        self.inject_system = getattr(config, "LESSON_INJECTION_SYSTEM", True) if config else True
        self.inject_session = getattr(config, "LESSON_INJECTION_SESSION", True) if config else True

        self._lessons: Dict[str, Lesson] = {}
        self._load()

    # ── 写入 ──────────────────────────────────────────────

    def add(self, scope: str, content: str, dimension: str = "通用",
            action: str = "", importance: float = 0.5, confidence: float = 0.7,
            source: str = "manual") -> Optional[Lesson]:
        """写入一条教训。

        置信度低于门槛直接丢弃——这是防止错误教训自我强化的第一道闸。
        同一 scope 下内容完全相同则合并（提升 confidence/importance），不重复堆积。
        """
        content = (content or "").strip()
        if not content or len(content) > 200:
            logger.debug("[lesson_store] 教训内容为空或过长，丢弃")
            return None
        if confidence < self.min_confidence:
            logger.debug(f"[lesson_store] 置信度 {confidence:.2f} 低于门槛 {self.min_confidence}，丢弃")
            return None

        fp = _fingerprint(content)
        for l in self._lessons.values():
            if l.fingerprint == fp and l.scope == scope:
                l.importance = max(l.importance, importance)
                l.confidence = max(l.confidence, confidence)
                l.last_accessed = time.time()
                self._persist()
                logger.info(f"[lesson_store] 教训合并: {content[:40]}")
                return l

        lesson = Lesson(
            lesson_id=str(uuid.uuid4())[:8],
            scope=scope,
            content=content,
            dimension=dimension,
            action=action,
            importance=importance,
            confidence=confidence,
            source=source,
            created_at=time.time(),
            last_accessed=time.time(),
            fingerprint=fp,
        )
        self._lessons[lesson.lesson_id] = lesson
        self._evict_if_needed()
        self._persist()
        logger.info(f"[lesson_store] 新增教训 [{scope}/{dimension}]: {content[:50]}")
        return lesson

    def _evict_if_needed(self):
        """系统级教训超上限时淘汰最弱的（confidence × importance 最低）"""
        system = [l for l in self._lessons.values() if l.scope == SYSTEM_SCOPE]
        if len(system) <= self.max_system:
            return
        system.sort(key=lambda l: l.confidence * l.importance)
        for l in system[:len(system) - self.max_system]:
            self._lessons.pop(l.lesson_id, None)
            logger.info(f"[lesson_store] 淘汰低价值系统级教训: {l.content[:40]}")

    # ── 召回 ──────────────────────────────────────────────

    def get_system_lessons(self) -> List[Lesson]:
        """系统级教训：每次无条件带上（数量受 max_system 限制）"""
        if not self.inject_enabled or not self.inject_system:
            return []
        out = [l for l in self._lessons.values() if l.scope == SYSTEM_SCOPE]
        # 排序键用 confidence×importance（稳定），不用 last_accessed——
        # 后者会在每次召回时被自身更新，导致排序依据自我漂移
        out.sort(key=lambda l: (l.confidence * l.importance, l.created_at), reverse=True)
        selected = out[:self.max_system]
        self._touch(selected)
        return selected

    def get_session_lessons(self, session_id: str, limit: int = None) -> List[Lesson]:
        """会话级教训：按会话召回"""
        if not self.inject_enabled or not self.inject_session or not session_id:
            return []
        limit = limit or self.max_session
        out = [l for l in self._lessons.values() if l.scope == session_id]
        # 同理，排序键不依赖会被读取动作修改的 last_accessed
        out.sort(key=lambda l: (l.confidence * l.importance, l.created_at), reverse=True)
        selected = out[:limit]
        self._touch(selected)
        return selected

    def _touch(self, lessons: List[Lesson]):
        """更新访问计数并落盘（访问统计需要在重启后保留）"""
        if not lessons:
            return
        now = time.time()
        for l in lessons:
            l.access_count += 1
            l.last_accessed = now
        self._persist()

    # ── 注入 ──────────────────────────────────────────────

    def render_for_prompt(self, session_lessons: List[Lesson] = None,
                          system_lessons: List[Lesson] = None) -> str:
        """渲染成 prompt 片段。

        教训与事实分属不同语义位，来源标记必须显式带上：
        - [系统级]  经过评测集验证的规则
        - [本次会话] 单次自评失败观察到的教训
        LLM 面对两者冲突时可自行权衡，避免低置信度教训压制高置信度规则。
        """
        lines = []
        for l in (system_lessons or []):
            lines.append(f"- [系统级·{l.dimension}] {l.content}")
        for l in (session_lessons or []):
            lines.append(f"- [本次会话·{l.dimension}] {l.content}")
        return "\n".join(lines) if lines else "（无）"

    def build_lesson_text(self, session_id: str = None) -> str:
        """一次性取出并渲染当前请求应注入的教训文本"""
        if not self.inject_enabled:
            return "（无）"
        system_ls = self.get_system_lessons()
        session_ls = self.get_session_lessons(session_id) if session_id else []
        return self.render_for_prompt(session_ls, system_ls)

    # ── 统计 ──────────────────────────────────────────────

    def get_stats(self) -> Dict:
        system = [l for l in self._lessons.values() if l.scope == SYSTEM_SCOPE]
        session = [l for l in self._lessons.values() if l.scope != SYSTEM_SCOPE]
        by_dimension = {}
        for l in self._lessons.values():
            by_dimension[l.dimension] = by_dimension.get(l.dimension, 0) + 1
        return {
            "total": len(self._lessons),
            "system": len(system),
            "session": len(session),
            "by_dimension": by_dimension,
            "inject_enabled": self.inject_enabled,
        }

    def delete(self, lesson_id: str) -> bool:
        lesson = self._lessons.pop(lesson_id, None)
        if not lesson:
            return False
        self._persist()
        logger.info(f"[lesson_store] 删除教训: {lesson_id}")
        return True

    # ── 持久化 ────────────────────────────────────────────

    def _persist(self):
        """原子落盘：先写临时文件再 os.replace 覆盖。

        直接覆写目标文件在进程中途崩溃时会留下半截 JSONL，
        os.replace 在同一文件系统内是原子操作，可避免读到损坏文件。
        """
        tmp_path = self.lessons_file + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                for l in self._lessons.values():
                    f.write(json.dumps(asdict(l), ensure_ascii=False) + "\n")
            os.replace(tmp_path, self.lessons_file)
        except Exception as e:
            logger.error(f"教训持久化失败: {e}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    def _load(self):
        if not os.path.exists(self.lessons_file):
            return
        loaded = 0
        skipped = 0
        try:
            with open(self.lessons_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        lesson = Lesson(**data)
                    except (json.JSONDecodeError, TypeError) as e:
                        # 单行损坏不应导致整个文件加载失败
                        skipped += 1
                        logger.warning(f"[lesson_store] 跳过损坏的教训记录: {e}")
                        continue
                    self._lessons[lesson.lesson_id] = lesson
                    loaded += 1
            logger.info(f"加载了 {loaded} 条教训" + (f"（跳过 {skipped} 条损坏记录）" if skipped else ""))
        except Exception as e:
            logger.error(f"教训加载失败: {e}")


# ── 全局单例 ────────────────────────────────────────────────
# LessonStore 持有文件句柄状态（_lessons 字典 + JSONL 覆写），
# 多个实例并发写会互相覆盖，因此必须全进程共用一个实例。
_lesson_store = None


def get_lesson_store(config=None, llm_client=None) -> LessonStore:
    """获取 LessonStore 单例（懒初始化）"""
    global _lesson_store
    if _lesson_store is None:
        if config is None:
            try:
                from ..core.config import settings as config
            except Exception:
                config = None
        _lesson_store = LessonStore(config=config, llm_client=llm_client)
    elif llm_client is not None and _lesson_store.llm is None:
        # 单例已存在但没有 LLM（首次创建时 LLM 未就绪），补上
        _lesson_store.llm = llm_client
    return _lesson_store
