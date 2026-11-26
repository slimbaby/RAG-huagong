"""
运行时注册表 + 后端权威意图路由（docs/10 §5.2 / §6）

- 判定完全在后端：前端零规则，只渲染 intent_choice 事件下发的候选卡（D10）。
- 规则流水线 P0→P1→P2 有序短路；疑问尾 + 办理意图 → 候选卡，不擅判。
- 判定器默认 rules；hybrid 本版自动降级（D11，可用例回归、一键降级）。
- 版本解析顺序：会话 / 单据锁定版本 > 灰度命中版本 > 当前 published（D13）。
- 单个定义加载失败只跳过该流程、保留上一版可用配置并告警，绝不让全站路由失效。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

from .model import INTENT_PHASES, FlowConfigError, FlowDefinition, compile_safe_regex, normalize_text

logger = logging.getLogger(__name__)

QUESTION_TAIL_RE = re.compile(r"(?:吗|么|呢)[？?]?\s*$|[？?]\s*$")
# 会话兜底收紧：闲聊 / 问候不续留在业务办理流程里
CHITCHAT_RE = re.compile(
    r"^(?:你好|您好|嗨|hi|hello|早上好|下午好|晚上好|谢谢|感谢|再见|拜拜|嗯+|哦|好的|ok|okay"
    r"|今天天气(?:真|很)?(?:好|不错)[啊呀呢吧！!。,.~\s]*"
    r"|(?:讲个笑话|闲聊一下|吃了吗|下班啦|周末愉快)[!！。,.~\s]*"
    r")[!！。,.~\s]*$",
    re.IGNORECASE,
)
# 配置未给句首正则时的框架兜底（与既有 ENTRY_STRONG_HEAD_RE 一致）
DEFAULT_HEAD_RE = re.compile(
    r"^\s*(?:师傅|你好|您好|嗨|嗯|那个)?[\s，,]*"
    r"(?:帮我|我要|我想|给我|麻烦|请帮我|能帮我|可以帮我|来)"
    r".{0,8}(?:申请|办理|帮我办|提交|预约|报备|登记|办一下|办|放行|弄|搞)"
)


def _json(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value


def _row_matches(row: Dict[str, Any], q: str, definition: FlowDefinition) -> Tuple[bool, bool]:
    """返回 (是否命中, 是否为疑问句导致的歧义)。"""
    when = str(row.get("when") or "any_pattern")
    patterns = [str(p) for p in (row.get("patterns") or [])]
    if when == "never":
        return False, False
    if when == "domain_words":
        words = [str(w) for w in (definition.intent.get("domain_words") or [])]
        return (any(w.lower() in q for w in words), False)
    if when == "head_apply_verb_and_domain":
        head_re = compile_safe_regex(patterns[0]) if patterns else DEFAULT_HEAD_RE
        if len(patterns) >= 2:
            domain_re = compile_safe_regex(patterns[1])
            domain_hit = bool(domain_re.search(q))
        else:
            words = [str(w).lower() for w in (definition.intent.get("domain_words") or [])]
            domain_hit = any(w in q for w in words) or not words
        hit = bool(head_re.search(q)) and domain_hit
        if not hit:
            return False, False
        return True, bool(QUESTION_TAIL_RE.search(q))
    for p in patterns:
        if compile_safe_regex(p).search(q):
            return True, False
    return False, False


def classify_text(text: str, definitions: List[FlowDefinition], *,
                  session_flow_id: Optional[str] = None,
                  forced_flow_id: Optional[str] = None,
                  forced_action: Optional[str] = None,
                  intent_resolved: bool = False,
                  session_completed: bool = False) -> Dict[str, Any]:
    """纯函数：按所有已发布配置的有序规则流水线判定，输出 flow_id + 动作。

    返回 {flow_id, action, needs_choice, candidates, rule_id, matched_text}。
    action ∈ action / readonly / status / attach / none；needs_choice 时 action='choice'。

    session_completed：该会话中该流程的单据已提交并在途（pending/approved）。
    此时关闭「无明确办理意图就续留流程」的兜底，避免提交后每轮问答都被
    劫持成办理并重出核对卡；用户明确说「再帮我申请…」仍由 P0/P2 命中 action。
    """
    q = normalize_text(text)
    phase_rank = {p: i for i, p in enumerate(INTENT_PHASES)}

    # 用户在选择卡上已做决定：force 只放宽歧义，不绕过后端权限与最终判定
    if forced_flow_id:
        target = next((d for d in definitions if d.flow_id == forced_flow_id), None)
        if target is not None:
            return {
                "flow_id": forced_flow_id,
                "action": forced_action or "action",
                "needs_choice": False,
                "candidates": [],
                "rule_id": "user_choice",
                "matched_text": q,
                "hint": str(target.intent.get("ambiguous_hint") or ""),
            }

    hits: List[Dict[str, Any]] = []
    ambiguous: List[Dict[str, Any]] = []
    for definition in definitions:
        pipeline = sorted(
            definition.intent.get("rule_pipeline") or [],
            key=lambda r: phase_rank.get(str(r.get("phase")), 99),
        )
        first_hit: Optional[Dict[str, Any]] = None
        for row in pipeline:
            try:
                matched, is_ambiguous = _row_matches(row, q, definition)
            except FlowConfigError as e:
                logger.warning(f"[flow_route] 规则 {row.get('id')} 编译失败，跳过: {e}")
                continue
            if not matched:
                continue
            item = {
                "flow_id": definition.flow_id,
                "name": definition.name,
                "outcome": str(row.get("outcome") or "action"),
                "rule_id": str(row.get("id") or ""),
                "phase": str(row.get("phase") or ""),
                "priority": int(definition.intent.get("tie_breaker") or 0),
                "hint": str(definition.intent.get("ambiguous_hint") or ""),
            }
            if is_ambiguous and not intent_resolved:
                ambiguous.append(item)
            if first_hit is None:
                first_hit = item
            if row.get("stop_on_match", True):
                break
        if first_hit:
            hits.append(first_hit)

    # 疑问 + 办理意图：交用户本人点选，不由模型猜
    if ambiguous and not intent_resolved:
        candidates = [
            {"flow_id": a["flow_id"], "name": a["name"], "action": "action", "hint": a["hint"]}
            for a in ambiguous
        ]
        return {
            "flow_id": ambiguous[0]["flow_id"], "action": "choice", "needs_choice": True,
            "candidates": candidates, "rule_id": ambiguous[0]["rule_id"],
            "matched_text": q, "hint": ambiguous[0]["hint"],
        }

    if not hits:
        # 会话兜底：同会话内后续轮次保持 flow_id（补材料），但只读咨询不吞进流程。
        # 单据已提交并在途时本流程视为已办结，后续无明确办理意图的闲聊/知识问答
        # 回到普通 RAG；要再办一张由 P0/P2 规则命中，不受此开关影响。
        if session_completed:
            return {"flow_id": None, "action": "none", "needs_choice": False,
                    "candidates": [], "rule_id": "session_completed",
                    "matched_text": q, "hint": ""}
        if session_flow_id and not CHITCHAT_RE.match(q) and not any(
            str(r.get("outcome")) == "readonly"
            for d in definitions if d.flow_id == session_flow_id
            for r in (d.intent.get("rule_pipeline") or [])
            if _safe_match(r, q)
        ):
            return {
                "flow_id": session_flow_id, "action": "action", "needs_choice": False,
                "candidates": [], "rule_id": "session_keep", "matched_text": q, "hint": "",
            }
        return {"flow_id": None, "action": "none", "needs_choice": False,
                "candidates": [], "rule_id": "none", "matched_text": q, "hint": ""}

    # 全局按 phase 优先（P0 句首办理 > P1 只读 > P2 通用），再按 tie_breaker
    hits.sort(key=lambda h: (
        phase_rank.get(h["phase"], 99),
        -h["priority"],
        0 if h["outcome"] == "action" else 1,
    ))
    best_phase = hits[0]["phase"]
    top = [h for h in hits if h["phase"] == best_phase]
    if len(top) > 1 and top[0]["priority"] == top[1]["priority"]:
        # 多流程撞车且优先级相同 → 候选卡
        candidates = [{"flow_id": h["flow_id"], "name": h["name"],
                       "action": h["outcome"], "hint": h["hint"]} for h in top]
        return {"flow_id": top[0]["flow_id"], "action": "choice", "needs_choice": True,
                "candidates": candidates, "rule_id": top[0]["rule_id"],
                "matched_text": q, "hint": "检测到多个流程，请确认要办理哪一个"}
    winner = top[0]
    return {"flow_id": winner["flow_id"], "action": winner["outcome"], "needs_choice": False,
            "candidates": [], "rule_id": winner["rule_id"], "matched_text": q,
            "hint": winner["hint"]}


def _safe_match(row: Dict[str, Any], q: str) -> bool:
    try:
        return any(compile_safe_regex(str(p)).search(q) for p in (row.get("patterns") or []))
    except FlowConfigError:
        return False


class FlowRegistry:
    """进程内已发布配置注册表；按版本号缓存，发布时失效热加载。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._definitions: Dict[str, FlowDefinition] = {}
        self._versions: Dict[str, int] = {}
        self._rollouts: Dict[str, Dict[str, Any]] = {}
        self._loaded = False
        self._errors: Dict[str, str] = {}

    @property
    def errors(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._errors)

    def invalidate(self) -> None:
        with self._lock:
            self._loaded = False

    async def load(self, *, force: bool = False) -> int:
        if self._loaded and not force:
            return len(self._definitions)
        from ..core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT flow_id, published_version, status, rollout FROM flow_definitions "
                "WHERE published_version > 0"
            )
            loaded: Dict[str, FlowDefinition] = {}
            versions: Dict[str, int] = {}
            rollouts: Dict[str, Dict[str, Any]] = {}
            errors: Dict[str, str] = {}
            for row in rows:
                fid, version = row["flow_id"], int(row["published_version"])
                try:
                    vrow = await conn.fetchrow(
                        "SELECT definition FROM flow_definition_versions "
                        "WHERE flow_id=$1 AND version=$2", fid, version,
                    )
                    if vrow is None:
                        raise FlowConfigError(f"发布版 v{version} 快照缺失")
                    raw = _json(vrow["definition"])
                    raw.setdefault("meta", {})["version"] = version
                    definition = FlowDefinition(raw)
                    # 编译期探针：正则 / 抽取器必须可编译，否则跳过该定义
                    for f in definition.fields:
                        if (f.get("extract") or {}).get("regex") or (f.get("extract") or {}).get("builtin"):
                            definition._extractor_pattern(f)
                    for r in definition.intent.get("rule_pipeline") or []:
                        for p in r.get("patterns") or []:
                            compile_safe_regex(str(p))
                    loaded[fid] = definition
                    versions[fid] = version
                    rollouts[fid] = _json(row["rollout"]) or {}
                except Exception as e:
                    errors[fid] = str(e)
                    logger.error(f"[flow_registry] 流程 {fid} 配置加载失败，跳过并保留上一版: {e}")
                    with self._lock:
                        if fid in self._definitions:
                            loaded[fid] = self._definitions[fid]
                            versions[fid] = self._versions[fid]
                            rollouts[fid] = self._rollouts.get(fid, {})
        with self._lock:
            self._definitions = loaded
            self._versions = versions
            self._rollouts = rollouts
            self._errors = errors
            self._loaded = True
        return len(loaded)

    def all_published(self) -> List[FlowDefinition]:
        with self._lock:
            return list(self._definitions.values())

    def published_version(self, flow_id: str) -> int:
        with self._lock:
            return int(self._versions.get(flow_id) or 0)

    def rollout(self, flow_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._rollouts.get(flow_id) or {})

    def get(self, flow_id: str, version: Optional[int] = None,
            *, username: str = "", role_id: str = "",
            session_version: Optional[int] = None,
            application_version: Optional[int] = None) -> Optional[FlowDefinition]:
        """按 D13 顺序解析版本：会话/单据锁定 > 灰度命中 > 当前 published。"""
        with self._lock:
            definition = self._definitions.get(flow_id)
            rollout = dict(self._rollouts.get(flow_id) or {})
            published = int(self._versions.get(flow_id) or 0)
        if definition is None:
            return None
        if version is not None:
            return definition if int(version) == published else None
        if rollout.get("mode") == "gray" and rollout.get("version"):
            users = [str(u) for u in (rollout.get("users") or [])]
            groups = [str(g) for g in (rollout.get("groups") or [])]
            if (username and username in users) or (role_id and role_id in groups):
                return definition
        return definition

    async def resolve(self, flow_id: str, *, username: str = "", role_id: str = "",
                      session_version: Optional[int] = None,
                      application_version: Optional[int] = None) -> Optional[FlowDefinition]:
        """会话 / 单据双重版本锁定优先：锁定版本按快照复现，不被中途发布打断。"""
        pinned = application_version if application_version is not None else session_version
        if pinned is not None:
            snap = await self.get_version(flow_id, int(pinned))
            if snap is not None:
                return snap
        return self.get(flow_id, username=username, role_id=role_id)

    async def get_version(self, flow_id: str, version: int) -> Optional[FlowDefinition]:
        """读指定版本完整快照（老单据按 definition_version 复现，§5.4）。"""
        from ..core.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT definition FROM flow_definition_versions WHERE flow_id=$1 AND version=$2",
                flow_id, int(version),
            )
        if row is None:
            return None
        raw = _json(row["definition"])
        raw.setdefault("meta", {})["version"] = int(version)
        return FlowDefinition(raw)


_registry = FlowRegistry()


def get_flow_registry() -> FlowRegistry:
    return _registry
