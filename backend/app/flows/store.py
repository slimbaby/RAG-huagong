"""
配置治理与持久化（docs/10 §6 / D2 / D12–D16）

- 草稿 / 发布态分离，运行时只读 published 版；草稿写错不影响线上。
- 草稿乐观锁 draft_revision（409 + 当前草稿）；发布带期望 published_version。
- 强制双人发布（D12）；改版按用户组灰度（D13）；异步只读 Git 备份（D14）；
  兼容视图 90 天 + 跨结（D15，见 seed.py）；hook 三态下线（D16）。
- 每次操作写 flow_audit_log（含 dry-run 与用例回归结果）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from . import hooks as hook_registry
from .model import FlowDefinition, to_json
from .registry import get_flow_registry
from .validator import validate_definition

logger = logging.getLogger(__name__)

BACKUP_ROOT = os.environ.get("FLOW_CONFIG_BACKUP_DIR", "./backups/flow_configs")
_SENSITIVE_KEY = ("api_key", "apikey", "secret", "password", "token", "private_key", "credential")


class FlowStoreError(Exception):
    def __init__(self, message: str, status_code: int = 400, extra: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.extra = extra or {}


def _load(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value


def _dump(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, default=str)


def json_diff(old: Any, new: Any, path: str = "") -> List[Dict[str, str]]:
    """递归 path 级 diff，供版本对比与后台展示。"""
    out: List[Dict[str, str]] = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            p = f"{path}.{k}" if path else str(k)
            if k not in old:
                out.append({"path": p, "kind": "added", "after": to_json(new[k])[:300]})
            elif k not in new:
                out.append({"path": p, "kind": "removed", "before": to_json(old[k])[:300]})
            else:
                out.extend(json_diff(old[k], new[k], p))
    elif isinstance(old, list) and isinstance(new, list):
        if old != new:
            out.append({"path": path or "[]", "kind": "changed",
                        "before": to_json(old)[:300], "after": to_json(new)[:300]})
    elif old != new:
        out.append({"path": path or "value", "kind": "changed",
                    "before": str(old)[:300], "after": str(new)[:300]})
    return out


def _strip_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("***REDACTED***" if any(s in str(k).lower() for s in _SENSITIVE_KEY)
                    else _strip_secrets(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


async def audit(flow_id: str, action: str, actor: str, detail: Optional[Dict[str, Any]] = None,
                application_id: str = "", from_status: str = "", to_status: str = "") -> None:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO flow_audit_log
               (flow_id, application_id, action, actor, from_status, to_status, detail)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)""",
            flow_id, application_id, action, actor, from_status, to_status, _dump(detail),
        )


async def list_flows() -> List[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT flow_id, published_version, status, draft_revision, updated_by, updated_at, rollout "
            "FROM flow_definitions ORDER BY flow_id"
        )
    out = []
    for r in rows:
        d = dict(r)
        d["rollout"] = _load(d.get("rollout"))
        if d.get("updated_at") and hasattr(d["updated_at"], "isoformat"):
            d["updated_at"] = d["updated_at"].isoformat()
        d["name"] = (_load((await get_draft(r["flow_id"])).get("definition") or {}).get("meta") or {}).get("name", "")
        out.append(d)
    return out


async def _row(flow_id: str):
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM flow_definitions WHERE flow_id=$1", flow_id)


async def get_draft(flow_id: str) -> Dict[str, Any]:
    row = await _row(flow_id)
    if row is None:
        return {"flow_id": flow_id, "definition": {}, "draft_revision": 0,
                "updated_by": "", "published_version": 0, "status": "draft", "exists": False}
    return {
        "flow_id": flow_id, "definition": _load(row["draft"]),
        "draft_revision": int(row["draft_revision"]), "updated_by": row["updated_by"],
        "published_version": int(row["published_version"]), "status": row["status"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else "",
        "exists": True,
    }


async def get_published(flow_id: str) -> Optional[Dict[str, Any]]:
    row = await _row(flow_id)
    if row is None or int(row["published_version"] or 0) <= 0:
        return None
    version = int(row["published_version"])
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        vrow = await conn.fetchrow(
            "SELECT * FROM flow_definition_versions WHERE flow_id=$1 AND version=$2",
            flow_id, version,
        )
    if vrow is None:
        return None
    definition = _load(vrow["definition"])
    definition.setdefault("meta", {})["version"] = version
    return {"flow_id": flow_id, "version": version, "definition": definition,
            "published_by": vrow["published_by"], "note": vrow["note"],
            "published_at": vrow["published_at"].isoformat() if vrow["published_at"] else ""}


async def save_draft(flow_id: str, definition: Dict[str, Any], *, expect_revision: int,
                     actor: str, run_validation: bool = False) -> Dict[str, Any]:
    """乐观锁：revision 不一致返回 409 并附当前草稿，由后提交者决定合并。"""
    from ..core.database import get_pool
    definition = dict(definition or {})
    definition.setdefault("meta", {})["flow_id"] = flow_id
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT draft_revision, draft FROM flow_definitions WHERE flow_id=$1 FOR UPDATE",
                flow_id,
            )
            current = int(row["draft_revision"]) if row else 0
            if current != int(expect_revision):
                raise FlowStoreError(
                    "草稿已被他人修改，请刷新后合并再提交", 409,
                    {"current_revision": current, "expect_revision": expect_revision,
                     "current_draft": _load(row["draft"]) if row else {},
                     "diff": json_diff(_load(row["draft"]) if row else {}, definition)[:20]},
                )
            if row is None:
                await conn.execute(
                    """INSERT INTO flow_definitions
                       (flow_id, draft, draft_revision, updated_by, status)
                       VALUES ($1,$2::jsonb,1,$3,'draft')""",
                    flow_id, _dump(definition), actor,
                )
            else:
                await conn.execute(
                    """UPDATE flow_definitions
                       SET draft=$2::jsonb, draft_revision=draft_revision+1,
                           updated_by=$3, updated_at=NOW()
                       WHERE flow_id=$1""",
                    flow_id, _dump(definition), actor,
                )
    result: Dict[str, Any] = {"flow_id": flow_id, "draft_revision": current + 1, "updated_by": actor}
    if run_validation:
        others = get_flow_registry().all_published()
        result["validation"] = validate_definition(definition, others=others)
    await audit(flow_id, "draft_save", actor, {"draft_revision": current + 1})
    return result


def _all_published_definitions(self_flow_id: str) -> List[FlowDefinition]:
    return [d for d in get_flow_registry().all_published() if d.flow_id != self_flow_id]


async def validate(flow_id: str, actor: str, *, definition: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """dry-run：不改变任何线上配置，结果写审计。"""
    if definition is None:
        definition = (await get_draft(flow_id))["definition"]
    result = validate_definition(definition, others=_all_published_definitions(flow_id))
    await audit(flow_id, "validate", actor,
                {"ok": result["ok"], "errors": result["errors"][:10],
                 "case_total": result["test_cases"].get("total", 0),
                 "case_passed": result["test_cases"].get("passed", 0)})
    return result


async def preview(flow_id: str, *, definition: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """预览：模拟确认卡 / 工具 schema / 菜单入口 / 一段对话，不落库。"""
    if definition is None:
        definition = (await get_draft(flow_id))["definition"]
    d = FlowDefinition(definition)
    sample = {str(f["key"]): (f.get("placeholder") or f"示例{f.get('label')}")
              for f in d.fields}
    sample["attachments"] = {str(a["key"]): "ATT_SAMPLE" for a in d.attachments}
    return {
        "render_config": d.render_config(),
        "card": d.build_card(sample),
        "tool_schema_properties": list(d.llm_slots_schema()["properties"].keys()),
        "system_prompt_preview": d.system_prompt()[:500],
        "entry_points": d.entry_points,
        "state_labels": d.state_labels,
        "transitions": d.transitions,
    }


async def publish(flow_id: str, *, actor: str, expect_version: int, note: str = "",
                  bootstrap: bool = False) -> Dict[str, Any]:
    """原子发布：双人校验 → dry-run 八项 → 快照 + diff + 审计 → 异步 Git 备份。"""
    draft = await get_draft(flow_id)
    definition = draft["definition"]
    if not definition:
        raise FlowStoreError("草稿为空，无法发布", 400)
    submitted_by = draft["updated_by"]
    if not bootstrap and submitted_by == actor:
        raise FlowStoreError("强制双人发布：草稿最后提交者与发布者必须为不同账号（D12）", 409)
    if int(draft["published_version"]) != int(expect_version):
        raise FlowStoreError(
            f"发布版本并发冲突：当前 published_version={draft['published_version']}", 409,
            {"current_version": draft["published_version"]},
        )
    validation = validate_definition(definition, others=_all_published_definitions(flow_id))
    if not validation["ok"]:
        await audit(flow_id, "publish_rejected", actor, {"errors": validation["errors"][:10]})
        raise FlowStoreError("dry-run 未通过，拒绝发布", 422, {"validation": validation})

    new_version = int(expect_version) + 1
    definition = dict(definition)
    definition.setdefault("meta", {})["version"] = new_version
    previous = await get_published(flow_id)
    diff = json_diff(previous["definition"] if previous else {}, definition)

    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            locked = await conn.fetchrow(
                "SELECT published_version FROM flow_definitions WHERE flow_id=$1 FOR UPDATE", flow_id,
            )
            if locked is None:
                await conn.execute(
                    """INSERT INTO flow_definitions
                       (flow_id, published_version, draft, draft_revision, updated_by, status)
                       VALUES ($1,0,$2::jsonb,$3,$4,'published')""",
                    flow_id, _dump(definition), int(draft["draft_revision"]), submitted_by,
                )
            else:
                if int(locked["published_version"]) != int(expect_version):
                    raise FlowStoreError("发布期间版本已被切换，请重试", 409)
                await conn.execute(
                    """UPDATE flow_definitions
                       SET published_version=$2, status='published', updated_at=NOW()
                       WHERE flow_id=$1""",
                    flow_id, new_version,
                )
            await conn.execute(
                """INSERT INTO flow_definition_versions
                   (flow_id, version, definition, diff, published_by, submitted_by, note)
                   VALUES ($1,$2,$3::jsonb,$4::jsonb,$5,$6,$7)
                   ON CONFLICT (flow_id, version) DO NOTHING""",
                flow_id, new_version, _dump(definition), _dump(diff[:500]),
                actor, submitted_by or ("system" if bootstrap else ""), note or "",
            )
    await audit(flow_id, "publish", actor,
                {"version": new_version, "submitted_by": submitted_by, "note": note,
                 "diff_count": len(diff), "case_passed": validation["test_cases"].get("passed", 0)})
    get_flow_registry().invalidate()
    await get_flow_registry().load(force=True)
    # D13：改版必灰度；首次发布可全量
    if int(expect_version) > 0:
        await set_rollout(flow_id, {"mode": "gray", "version": new_version,
                                   "users": [], "groups": [], "observe_days": 1,
                                   "note": "配置改版自动进入灰度（D13）"}, actor, auto=True)
    # D14：异步只读 Git 快照，失败只告警不阻断发布
    asyncio.create_task(_export_snapshot(flow_id, new_version, definition, diff, actor, note))
    return {"flow_id": flow_id, "version": new_version, "published_by": actor,
            "submitted_by": submitted_by, "diff_count": len(diff), "validation": validation}


async def _export_snapshot(flow_id: str, version: int, definition: Dict[str, Any],
                           diff: List[Dict[str, str]], actor: str, note: str) -> None:
    """导出到独立只读快照目录；若该目录本身是 Git 仓库则自动 commit。"""
    try:
        os.makedirs(BACKUP_ROOT, exist_ok=True)
        payload = {
            "flow_id": flow_id, "version": version, "published_by": actor,
            "exported_at": datetime.now().isoformat(), "note": note,
            "definition": _strip_secrets(definition), "diff": diff[:500],
        }
        path = os.path.join(BACKUP_ROOT, f"{flow_id}_v{version}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        if os.path.isdir(os.path.join(BACKUP_ROOT, ".git")):
            proc = await asyncio.create_subprocess_exec(
                "git", "add", os.path.basename(path),
                cwd=BACKUP_ROOT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            proc = await asyncio.create_subprocess_exec(
                "git", "commit", "-m", f"flow config {flow_id} v{version}",
                cwd=BACKUP_ROOT, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        logger.info(f"[flow_store] 配置快照已导出 {path}")
    except Exception as e:
        logger.warning(f"[flow_store] 配置快照导出失败（不阻断发布）: {e}")


async def rollback(flow_id: str, version: int, actor: str) -> Dict[str, Any]:
    row = await _row(flow_id)
    if row is None:
        raise FlowStoreError("流程不存在", 404)
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM flow_definition_versions WHERE flow_id=$1 AND version=$2",
            flow_id, int(version),
        )
        if not exists:
            raise FlowStoreError(f"版本 v{version} 不存在", 404)
        await conn.execute(
            "UPDATE flow_definitions SET published_version=$2, status='published', "
            "rollout='{}'::jsonb, updated_at=NOW() WHERE flow_id=$1",
            flow_id, int(version),
        )
    await audit(flow_id, "rollback", actor, {"version": int(version)})
    get_flow_registry().invalidate()
    await get_flow_registry().load(force=True)
    return {"flow_id": flow_id, "published_version": int(version)}


async def versions(flow_id: str) -> List[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT version, published_by, submitted_by, published_at, note, diff "
            "FROM flow_definition_versions WHERE flow_id=$1 ORDER BY version DESC", flow_id,
        )
    out = []
    for r in rows:
        d = dict(r)
        d["diff"] = _load(d["diff"])
        if d.get("published_at") and hasattr(d["published_at"], "isoformat"):
            d["published_at"] = d["published_at"].isoformat()
        out.append(d)
    return out


async def version_diff(flow_id: str, version: int) -> Dict[str, Any]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT diff, definition FROM flow_definition_versions WHERE flow_id=$1 AND version=$2",
            flow_id, int(version),
        )
    if row is None:
        raise FlowStoreError("版本不存在", 404)
    return {"flow_id": flow_id, "version": int(version), "diff": _load(row["diff"]),
            "definition": _load(row["definition"])}


async def set_rollout(flow_id: str, config: Dict[str, Any], actor: str,
                      *, auto: bool = False) -> Dict[str, Any]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE flow_definitions SET rollout=$2::jsonb WHERE flow_id=$1",
            flow_id, _dump(config),
        )
    if not auto:
        await audit(flow_id, "rollout", actor, config)
    get_flow_registry().invalidate()
    return {"flow_id": flow_id, "rollout": config}


async def get_rollout(flow_id: str) -> Dict[str, Any]:
    row = await _row(flow_id)
    return _load(row["rollout"]) if row else {}


async def save_test_cases(flow_id: str, cases: List[Dict[str, Any]], actor: str,
                          expect_revision: int) -> Dict[str, Any]:
    """用例表在 DB，由业务同事在后台维护（D8）。写入走草稿乐观锁。"""
    draft = await get_draft(flow_id)
    definition = dict(draft["definition"] or {})
    definition.setdefault("intent", {})["test_cases"] = cases
    return await save_draft(flow_id, definition, expect_revision=expect_revision, actor=actor)


async def run_test_cases(flow_id: str, actor: str, *, cases: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    draft = await get_draft(flow_id)
    definition = dict(draft["definition"] or {})
    if cases is not None:
        definition.setdefault("intent", {})["test_cases"] = cases
    from .registry import classify_text
    peers = _all_published_definitions(flow_id)
    definitions = [FlowDefinition(definition)] + peers
    total = passed = 0
    failed = []
    for i, case in enumerate(definition.get("intent", {}).get("test_cases") or []):
        total += 1
        decision = classify_text(str(case.get("text") or ""), definitions)
        got_action = "choice" if decision["needs_choice"] else decision["action"]
        expect_action = str(case.get("expect_action") or "")
        expect_flow = str(case.get("expect_flow_id") or flow_id)
        ok = decision["flow_id"] == expect_flow and (not expect_action or got_action == expect_action)
        if ok:
            passed += 1
        else:
            failed.append({"index": i, "text": case.get("text"), "expect": {"flow_id": expect_flow, "action": expect_action},
                           "got": {"flow_id": decision["flow_id"], "action": got_action}})
    result = {"total": total, "passed": passed, "failed": failed, "ok": not failed}
    await audit(flow_id, "test_cases_run", actor, result)
    return result


async def hook_references(name: str) -> Dict[str, Any]:
    """D16：全量扫描已发布版本快照 + 在途单据，判断能否进入 retired。"""
    from ..core.database import get_pool
    pool = await get_pool()
    published_refs: List[Dict[str, Any]] = []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT flow_id, version, definition FROM flow_definition_versions ORDER BY flow_id, version"
        )
        for r in rows:
            definition = _load(r["definition"])
            if any(str(h.get("name")) == name for h in (definition.get("hooks") or [])):
                published_refs.append({"flow_id": r["flow_id"], "version": int(r["version"])})
        open_apps = await conn.fetchval(
            "SELECT COUNT(*) FROM flow_applications WHERE status IN ('draft','pending')"
        )
    return {"name": name, "published_references": published_refs,
            "reference_count": len(published_refs), "open_applications": int(open_apps or 0)}


async def list_audit(flow_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        if flow_id:
            rows = await conn.fetch(
                "SELECT * FROM flow_audit_log WHERE flow_id=$1 ORDER BY created_at DESC, id DESC LIMIT $2",
                flow_id, min(max(limit, 1), 500),
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM flow_audit_log ORDER BY created_at DESC, id DESC LIMIT $1",
                min(max(limit, 1), 500),
            )
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = _load(d.get("detail"))
        if d.get("created_at") and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out
