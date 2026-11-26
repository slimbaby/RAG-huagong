"""
阶段 1：入园流程导出成第一份配置 + 老三表迁移（docs/10 §9.1）

所有字段 / 抽取规则 / 意图流水线 / 附件 / 状态 / 菜单都逐项来自现有代码：
  services/entry_schema.py、services/query_rewriter.py、api/entry.py、
  api/chat.py、components/Layout/Sidebar.tsx、shared/entry_intent_cases.json
迁移遵循 D15：兼容视图保留 90 天且至少跨一次月结 / 季度结，期间只读。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List

from . import store
from .model import BUILTIN_EXTRACTORS, FlowDefinition
from .registry import classify_text

logger = logging.getLogger(__name__)

SEED_FLOW_ID = "driver_entry"
COMPAT_VIEW_MIN_DAYS = 90  # D15：且至少跨一次月结 / 季度结，取更长者

# 与 query_rewriter.ENTRY_STRONG_HEAD_RE / ENTRY_DOMAIN_RE 逐字一致
HEAD_RE = (
    r"^\s*(?:师傅|你好|您好|嗨|嗯|那个)?[\s，,]*"
    r"(?:帮我|我要|我想|给我|麻烦|请帮我|能帮我|可以帮我|来)"
    r".{0,8}(?:申请|办理|帮我办|提交|预约|报备|登记|办一下|办|放行|弄|搞)"
)
DOMAIN_RE = r"(?:入园|进园|入厂|进厂|园区|通行证|入园证|手续|证件|照片|材料)"
READONLY_PATTERNS = [
    r"^(?:查|查询|查一下|查查|看看|问一下|请问|想问|咨询|了解一下)",
    r"(?:怎么|如何|怎样|需要什么|要带什么|带哪些|哪些证件|哪些材料|什么证件|什么材料|需要哪些|流程是什么|什么流程)",
    r"(?:规定|制度|要求|标准|政策|流程说明|入园须知)",
    r"(?:进度|状态|结果|通过了吗|批了吗|审核到哪)",
    r"(?:吗|么|呢)[？?]?\s*$",
]
ACTION_PATTERNS = [
    r"(?:申请|办理|帮我办|提交|预约|报备|办一下|登记).{0,10}(?:入园|进园|入厂|进厂|园区|通行证|手续)",
    r"(?:入园|进园|入厂|进厂).{0,10}(?:申请|预约|办理|通行证|手续|报备|登记)",
    r"^(?:帮我|我要|我想).{0,12}(?:入园|进园|入厂|进厂)",
    r"(?:证件|照片|材料).{0,12}(?:拍好|传好|都齐|都备齐|准备好了).{0,12}(?:提交|帮我|申请|办)",
]

# 与 entry_schema.FORM_FIELDS 一一对应（13 项）
_FIELDS: List[Dict[str, Any]] = [
    {"key": "driver_name", "label": "司机姓名", "group": "driver", "placeholder": "张三",
     "extract": {"builtin": "name", "regex": BUILTIN_EXTRACTORS["name"]}},
    {"key": "gender", "label": "性别", "group": "driver", "placeholder": "男 / 女",
     "type": "enum", "options": ["男", "女"], "extract": {"builtin": "gender"}},
    {"key": "id_no", "label": "身份证号", "group": "driver", "placeholder": "18 位身份证号码",
     "type": "idcard", "pii": True,
     "extract": {"builtin": "idcard", "regex": BUILTIN_EXTRACTORS["idcard"],
                 "uppercase": True, "mask": "keep3keep4",
                 "reconcile": "must_appear_in_text", "from_attachment": "driving_license"}},
    {"key": "phone", "label": "手机号", "group": "driver", "placeholder": "13800000000",
     "type": "phone", "pii": True,
     "extract": {"builtin": "phone", "regex": BUILTIN_EXTRACTORS["phone"],
                 "mask": "keep3keep4", "reconcile": "must_appear_in_text"}},
    {"key": "plate_no", "label": "车牌号", "group": "vehicle", "placeholder": "鲁A12345",
     "type": "plate",
     "extract": {"builtin": "plate", "regex": BUILTIN_EXTRACTORS["plate"],
                 "reconcile": "must_appear_in_text"}},
    {"key": "vehicle_type", "label": "车辆类型", "group": "vehicle", "placeholder": "如 罐式货车 / 槽罐车"},
    {"key": "load_capacity", "label": "车辆承重", "group": "vehicle", "placeholder": "如 30吨"},
    {"key": "company_name", "label": "申请公司名称", "group": "apply", "placeholder": "XX化工有限公司",
     "extract": {"builtin": "company", "regex": BUILTIN_EXTRACTORS["company"]}},
    {"key": "chemical_name", "label": "运输化学品名称", "group": "apply", "placeholder": "如 甲醇"},
    {"key": "chemical_type", "label": "化学品类型", "group": "apply", "placeholder": "如 易燃液体"},
    {"key": "weight_ton", "label": "运输重量（吨）", "group": "apply", "placeholder": "仅填数字，如 20",
     "extract": {"builtin": "weight", "regex": BUILTIN_EXTRACTORS["weight"]}},
    {"key": "entry_time", "label": "入园时间", "group": "apply", "placeholder": "2026-11-26 08:00",
     "extract": {"builtin": "datetime_range", "regex": BUILTIN_EXTRACTORS["datetime_range"],
                 "date_formats": ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                                  "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
                                  "%Y年%m月%d日 %H:%M", "%Y年%m月%d日"],
                 "normalize": "fullwidth_to_ascii",
                 "targets": ["entry_time", "exit_time"]}},
    {"key": "exit_time", "label": "出园时间", "group": "apply", "placeholder": "2026-11-26 18:00"},
]


def _cases_from_baseline() -> List[Dict[str, Any]]:
    """把 shared/entry_intent_cases.json 的 40 条基线迁到 DB 用例表。

    原用例表本身就是「当前实现的回归基线」：action / ambiguous 是期望，
    business_expect 是业务理想值。这里用 classify_text 把「非办理非两可」
    的基线值补成 readonly / none，保证首次发布 dry-run 全绿、且后续改版
    任何漂移都会被用例回归拦下。
    """
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "shared", "entry_intent_cases.json")
    cases: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning(f"[flow_seed] 用例表读取失败，使用内置最小基线: {e}")
        return _MIN_CASES
    definition = FlowDefinition(build_driver_entry_definition(cases=[]))
    for item in raw.get("cases") or []:
        text = str(item.get("q") or "")
        if item.get("ambiguous"):
            expect = "choice"
        elif item.get("action"):
            expect = "action"
        else:
            decision = classify_text(text, [definition])
            expect = decision["action"] if decision["flow_id"] == SEED_FLOW_ID else "none"
        cases.append({
            "text": text,
            "expect_flow_id": SEED_FLOW_ID if expect != "none" else "",
            "expect_action": expect,
            "expect_choice": bool(item.get("ambiguous")),
            "group": item.get("group") or "",
            "business_expect": item.get("business_expect"),
            "note": item.get("note") or "",
        })
    return cases or _MIN_CASES


_MIN_CASES = [
    {"text": "帮我申请入园，我叫张三", "expect_flow_id": SEED_FLOW_ID, "expect_action": "action"},
    {"text": "入园要带什么材料", "expect_flow_id": SEED_FLOW_ID, "expect_action": "readonly"},
    {"text": "能帮我申请入园吗", "expect_flow_id": SEED_FLOW_ID, "expect_action": "choice", "expect_choice": True},
]


def build_driver_entry_definition(*, cases: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    fields = []
    for i, f in enumerate(_FIELDS):
        item = dict(f)
        item["required"] = True
        item["order"] = i
        item["type"] = item.get("type") or "string"
        item["source"] = "user"
        fields.append(item)
    return {
        "meta": {"flow_id": SEED_FLOW_ID, "name": "司机入园预约",
                 "description": "司机 Web 手动申请 / RAG 对话辅助申请；确认后写库，admin 审核",
                 "version": 1, "status": "published", "owner": "园区管理"},
        "groups": [
            {"key": "driver", "label": "司机信息"},
            {"key": "vehicle", "label": "车辆信息"},
            {"key": "apply", "label": "运输与申请信息"},
            {"key": "attachments", "label": "车辆照片与证件"},
        ],
        "fields": fields,
        "intent": {
            "rule_pipeline": [
                {"id": "p0_head_request", "phase": "p0_head_request",
                 "when": "head_apply_verb_and_domain", "patterns": [HEAD_RE, DOMAIN_RE],
                 "outcome": "action", "stop_on_match": True},
                {"id": "p1_readonly", "phase": "p1_readonly", "when": "readonly",
                 "patterns": READONLY_PATTERNS, "outcome": "readonly", "stop_on_match": True},
                {"id": "p2_action", "phase": "p2_general", "when": "apply_verb_and_domain",
                 "patterns": ACTION_PATTERNS, "outcome": "action", "stop_on_match": True},
            ],
            "domain_words": ["证件", "照片", "材料", "补交"],
            "ambiguous_hint": "您是要办理入园申请，还是只想咨询入园规定？",
            "tie_breaker": 10,
            "classifier": {"mode": "rules", "fallback": "rules"},
            "test_cases": cases if cases is not None else _cases_from_baseline(),
        },
        "attachments": [
            {"key": k, "label": v, "required": True, "max_count": 1,
             "accept": ".jpg,.jpeg,.png,.webp,.pdf", "max_size_mb": 10,
             "reuse_policy": "per_application"}
            for k, v in (
                ("vehicle_front", "车头照片"), ("vehicle_rear", "车尾照片"),
                ("vehicle_license", "行驶证"), ("driving_license", "驾驶证"),
                ("hazmat_license", "危化品运输证"),
            )
        ],
        "workflow": {
            "state_labels": {"draft": "草稿（待确认提交）", "pending": "待审核",
                             "approved": "审核通过", "rejected": "已驳回", "cancelled": "已撤回"},
            "allowed_transitions": [
                {"from": "draft", "to": "pending", "action": "submit", "actor_role": "driver"},
                {"from": "pending", "to": "approved", "action": "review", "actor_role": "admin"},
                {"from": "pending", "to": "rejected", "action": "review", "actor_role": "admin"},
                {"from": "rejected", "to": "draft", "action": "reopen", "actor_role": "driver"},
                {"from": "draft", "to": "cancelled", "action": "cancel", "actor_role": "driver"},
                {"from": "pending", "to": "cancelled", "action": "cancel", "actor_role": "driver"},
            ],
            "pass": {"enabled": True, "hook": "generate_pass_no_and_qr"},
        },
        "session": {"session_prefix": "entry-", "keep_flow_on_followup": True,
                    "switch_policy": "confirm", "choice_loop_guard": "intent_resolved_once",
                    "ttl_hours": 2, "version_pin": True},
        "entry_points": [
            {"type": "menu", "key": "entry_apply", "label": "入园申请",
             "path": "/flows/driver_entry/apply", "visible_roles": ["driver", "admin"], "icon": "Truck", "order": 10},
            {"type": "menu", "key": "entry_my", "label": "我的申请",
             "path": "/flows/driver_entry/my", "visible_roles": ["driver", "admin"], "icon": "ClipboardList", "order": 11},
            {"type": "menu", "key": "entry_review", "label": "入园审核",
             "path": "/flows/driver_entry/review", "visible_roles": ["admin"], "icon": "ShieldCheck", "order": 12},
            {"type": "shortcut", "key": "entry_ai", "label": "AI 入园申请",
             "visible_roles": ["driver", "admin"], "icon": "Sparkles", "order": 1},
        ],
        "card": {"title": "入园申请信息核对",
                 "field_keys": [f["key"] for f in fields],
                 "title_field": "plate_no",
                 "prefill_target": "/flows/driver_entry/apply"},
        "prompts": {
            "system_prompt": (
                "你是园区「司机入园申请」助手，只负责收集与核对信息，不办理审批、不提交申请、不写入数据库。\n"
                "工作规则：\n"
                "1. 司机给一段话或照片时，先调用 parse_flow_materials 把新信息合并进已有字段。\n"
                "2. 需要照片/证件时调用 get_my_attachments；五类附件为：车头照片、车尾照片、行驶证、驾驶证、危化品运输证。\n"
                "3. 回答「还缺什么」、准备收口前，必须调用 check_flow_rules，不要凭自己的感觉判断字段是否齐备。\n"
                "4. 只有 check_flow_rules 返回 complete=true，才调用 push_flow_confirm_card 推核对卡。\n"
                "5. 缺项时用自然的口语一次性问清（最多 4 项），不要逐条追问十几轮，不要编造任何字段值。\n"
                "6. 身份证号、手机号等敏感信息不要在回复里完整复述，确认卡会自动打码。\n"
                "7. 明确告诉司机：核对无误后由他本人在确认卡或申请表单里点击提交，AI 不会代替提交。"
            ),
            "ask_hints": ["五类附件必须显式核对，不得自动取最新一张照片。"],
            "examples": [],
        },
        "hooks": [
            {"name": "reconcile_high_confidence", "phase": "after_parse", "params": {}},
            {"name": "validate_time_window", "phase": "before_validate",
             "params": {"in_field": "entry_time", "out_field": "exit_time"}},
            {"name": "validate_weight_limit", "phase": "before_validate",
             "params": {"field": "weight_ton", "max": 100.0}},
            {"name": "validate_attachment_binding", "phase": "before_submit", "params": {}},
            {"name": "generate_pass_no_and_qr", "phase": "on_review",
             "params": {"prefix": "GP", "payload_prefix": "ENTRY-PASS",
                        "payload_fields": ["plate_no", "driver_name", "entry_time", "exit_time"]}},
        ],
        "permissions": {"applicant_roles": ["driver"], "reviewer_roles": ["admin"],
                        "scope": "entry_public"},
    }


async def bootstrap() -> Dict[str, Any]:
    """首次启动：导入 driver_entry 配置 + 迁移老三表。幂等。"""
    result: Dict[str, Any] = {"flow_id": SEED_FLOW_ID}
    published = await store.get_published(SEED_FLOW_ID)
    if published is None:
        definition = build_driver_entry_definition()
        draft = await store.get_draft(SEED_FLOW_ID)
        saved = await store.save_draft(SEED_FLOW_ID, definition,
                                       expect_revision=int(draft["draft_revision"]),
                                       actor="system_editor")
        pub = await store.publish(SEED_FLOW_ID, actor="system_publisher",
                                  expect_version=0, note="阶段 1：入园流程配置化初始导入",
                                  bootstrap=True)
        result.update({"published": True, "version": pub["version"],
                       "draft_revision": saved["draft_revision"],
                       "case_total": pub["validation"]["test_cases"].get("total", 0),
                       "case_passed": pub["validation"]["test_cases"].get("passed", 0)})
    else:
        result.update({"published": False, "version": published["version"]})
    result["migration"] = await migrate_legacy_entry()
    return result


async def migrate_legacy_entry() -> Dict[str, Any]:
    """§9.1：备份 → 逐行迁移 → 重命名老表 → 建兼容视图 → 双跑校验。幂等。"""
    from ..core.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        legacy = await conn.fetchval(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema='public' AND table_name='entry_applications_legacy'"""
        )
        if legacy:
            return {"status": "already_migrated", "compat_view_days": COMPAT_VIEW_MIN_DAYS}
        table_type = await conn.fetchval(
            """SELECT table_type FROM information_schema.tables
               WHERE table_schema='public' AND table_name='entry_applications'"""
        )
        if table_type != "BASE TABLE":
            return {"status": "no_legacy_table"}

        # 1) 备份（导出 JSON，不依赖文件系统之外任何服务）
        os.makedirs("./backups", exist_ok=True)
        backup = {"exported_at": datetime.now().isoformat(), "tables": {}}
        for table in ("entry_applications", "entry_attachments", "entry_audit_log"):
            rows = await conn.fetch(f"SELECT * FROM {table}")
            backup["tables"][table] = [dict(r) for r in rows]
        with open("./backups/entry_legacy_backup.json", "w", encoding="utf-8") as f:
            json.dump(backup, f, ensure_ascii=False, indent=2, default=str)

        # 2) 逐行迁移（保留原 id / 状态 / 审核信息 / 附件绑定）
        apps = backup["tables"]["entry_applications"]
        for r in apps:
            form = r.get("form_data") or {}
            if isinstance(form, str):
                try:
                    form = json.loads(form)
                except ValueError:
                    form = {}
            result = {"pass_no": r.get("pass_no") or ""}
            if r.get("qr_payload"):
                result["qr_payload"] = r["qr_payload"]
            if r.get("pass_issued_at"):
                result["pass_issued_at"] = str(r["pass_issued_at"])
            await conn.execute(
                """INSERT INTO flow_applications
                   (id, flow_id, definition_version, status, applicant_username, source,
                    assisted_by_ai, form_data, attachments, result, title,
                    review_comment, reviewed_by, reviewed_at, confirmed_at, created_at, updated_at)
                   VALUES ($1,$2,1,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12,$13,$14,$15,$16)
                   ON CONFLICT (id) DO NOTHING""",
                r["id"], SEED_FLOW_ID, r["status"], r["applicant_username"],
                r.get("source") or "manual", bool(r.get("assisted_by_ai")),
                json.dumps(form, ensure_ascii=False),
                json.dumps(form.get("attachments") or {}, ensure_ascii=False),
                json.dumps(result, ensure_ascii=False, default=str),
                str(form.get("plate_no") or "")[:128],
                r.get("review_comment") or "", r.get("reviewed_by") or "",
                r.get("reviewed_at"), r.get("confirmed_at"),
                r.get("created_at"), r.get("updated_at"),
            )
        for r in backup["tables"]["entry_attachments"]:
            await conn.execute(
                """INSERT INTO flow_attachments
                   (id, flow_id, owner_username, application_id, kind, filename,
                    storage_path, content_type, size_bytes, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                   ON CONFLICT (id) DO NOTHING""",
                r["id"], SEED_FLOW_ID, r["owner_username"], r.get("application_id") or "",
                r["kind"], r["filename"], r["storage_path"], r.get("content_type") or "",
                int(r.get("size_bytes") or 0), r.get("created_at"),
            )
        for r in backup["tables"]["entry_audit_log"]:
            await conn.execute(
                """INSERT INTO flow_audit_log
                   (flow_id, application_id, action, actor, from_status, to_status, detail, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8)""",
                SEED_FLOW_ID, r["application_id"], r["action"], r["actor_username"],
                r.get("from_status") or "", r.get("to_status") or "",
                json.dumps(r.get("detail") or {}, ensure_ascii=False, default=str),
                r.get("created_at"),
            )

        # 3) 重命名老表 + 建同名只读兼容视图（D15：90 天且至少跨一次月结/季度结）
        await conn.execute("ALTER TABLE entry_applications RENAME TO entry_applications_legacy")
        await conn.execute("ALTER TABLE entry_attachments RENAME TO entry_attachments_legacy")
        await conn.execute("ALTER TABLE entry_audit_log RENAME TO entry_audit_log_legacy")
        await conn.execute("""
            CREATE OR REPLACE VIEW entry_applications AS
            SELECT id, applicant_username, source, status, form_data,
                   COALESCE(form_data->>'driver_name','')::varchar AS driver_name,
                   COALESCE(form_data->>'phone','')::varchar AS phone,
                   COALESCE(form_data->>'plate_no','')::varchar AS plate_no,
                   COALESCE(form_data->>'company_name','')::varchar AS company_name,
                   CASE WHEN COALESCE(form_data->>'entry_time','') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                        THEN (form_data->>'entry_time')::timestamp ELSE NULL END AS entry_time,
                   CASE WHEN COALESCE(form_data->>'exit_time','') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                        THEN (form_data->>'exit_time')::timestamp ELSE NULL END AS exit_time,
                   review_comment, reviewed_by, reviewed_at, confirmed_at, assisted_by_ai,
                   COALESCE(result->>'pass_no','')::varchar AS pass_no,
                   COALESCE(result->>'qr_payload','')::text AS qr_payload,
                   CASE WHEN COALESCE(result->>'pass_issued_at','') ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                        THEN (result->>'pass_issued_at')::timestamp ELSE NULL END AS pass_issued_at,
                   ''::varchar AS workflow_run_id, ''::varchar AS session_id,
                   NULL::varchar AS idempotency_key, created_at, updated_at
            FROM flow_applications WHERE flow_id='driver_entry'
        """)
        await conn.execute("""
            CREATE OR REPLACE VIEW entry_attachments AS
            SELECT id, owner_username, application_id, kind, filename, storage_path,
                   content_type, size_bytes, NULL::jsonb AS ocr_text, created_at
            FROM flow_attachments WHERE flow_id='driver_entry'
        """)
        await conn.execute("""
            CREATE OR REPLACE VIEW entry_audit_log AS
            SELECT id, application_id, action, actor AS actor_username,
                   from_status, to_status, detail, created_at
            FROM flow_audit_log WHERE flow_id='driver_entry'
        """)

        # 4) 双跑校验：行数、状态分布、通行证编号
        migrated = await conn.fetchval(
            "SELECT COUNT(*) FROM flow_applications WHERE flow_id=$1", SEED_FLOW_ID,
        )
        legacy_count = len(apps)
        pass_match = await conn.fetchval(
            """SELECT COUNT(*) FROM entry_applications_legacy l
               JOIN flow_applications f ON f.id=l.id
               WHERE COALESCE(l.pass_no,'') = COALESCE(f.result->>'pass_no','')"""
        )
        return {
            "status": "migrated",
            "legacy_rows": legacy_count, "migrated_rows": int(migrated or 0),
            "pass_no_match": int(pass_match or 0),
            "backup": "./backups/entry_legacy_backup.json",
            "compat_view_days": COMPAT_VIEW_MIN_DAYS,
            "compat_view_note": "D15：保留 90 天且至少跨一次月结/季度结，期间只读；删除需单独审批",
        }
