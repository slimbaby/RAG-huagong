"""
ERP 模拟层 —— 用 Excel 文件代替真实 ERP

⚠️ 这是在真实 ERP 接通前的临时实现。真实接入时只需把本模块的
   读写函数换成 ERP 的 API/RPC/数据库调用，workflow YAML 不用改。

存储文件（默认 data/ 下）：
    ERP待排产池.xlsx    —— workflow 写入，用户下单产生
    生产计划.xlsx       —— 定时排产任务的输出，排产人员看这个

幂等约定：待排产池的「幂等键」列以 run_id:step_id 为键。
LangGraph 恢复执行时节点会重入，没有幂等键会重复建单。
"""

import logging
import os
import threading
from datetime import date, datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Excel 读写是阻塞 IO，统一用一把锁串行化，避免并发写坏文件
_excel_lock = threading.Lock()

_PENDING_SHEET = "待排产池"
_PLAN_SHEET = "生产计划"

PENDING_HEADERS = [
    "单号", "幂等键", "产品", "数量(吨)", "交期", "客户", "联系人", "备注",
    "状态", "来源run_id", "创建时间", "排产日期", "产线",
]

PLAN_HEADERS = ["排产序号", "单号", "产品", "数量(吨)", "交期", "客户", "排产日期", "产线"]


def _data_dir() -> str:
    # 环境变量优先（测试隔离用），否则读配置，最后兜底 ./data
    base = os.environ.get("ERP_DATA_DIR")
    if not base:
        try:
            from ...core.config import settings
            base = settings.ERP_DATA_DIR
        except Exception:
            base = "./data"
    os.makedirs(base, exist_ok=True)
    return base


def _pending_path() -> str:
    return os.path.join(_data_dir(), "ERP待排产池.xlsx")


def _plan_path() -> str:
    return os.path.join(_data_dir(), "生产计划.xlsx")


def _openpyxl():
    try:
        from openpyxl import Workbook, load_workbook
        return Workbook, load_workbook
    except ImportError:
        logger.error("需要 openpyxl：pip install openpyxl")
        raise


def _load_rows(path: str, sheet: str) -> List[Dict[str, Any]]:
    """读整张表为 dict 列表；文件不存在返回空列表"""
    Workbook, load_workbook = _openpyxl()
    if not os.path.exists(path):
        return []
    wb = load_workbook(path)
    if sheet not in wb.sheetnames:
        wb.close()
        return []
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return []
    headers = [str(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        if r is None or all(c is None or str(c).strip() == "" for c in r):
            continue
        out.append({headers[i]: r[i] for i in range(min(len(headers), len(r)))})
    return out


def _write_rows(path: str, sheet: str, headers: List[str], rows: List[List[Any]]) -> None:
    """整表覆写。调用方必须已持锁。"""
    Workbook, _ = _openpyxl()
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    ws.append(headers)
    for r in rows:
        ws.append(r)
    ws.freeze_panes = "A2"
    #列宽按表头长度给个下限，避免长文本挤成 ####
    for idx, h in enumerate(headers, start=1):
        ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = max(12, len(h) * 2)
    wb.save(path)
    wb.close()


def _next_order_no(rows: List[Dict[str, Any]]) -> str:
    """按现有最大单号递增生成：MO202610210001"""
    prefix = "MO" + datetime.now().strftime("%Y%m%d")
    max_seq = 0
    for r in rows:
        no = str(r.get("单号") or "")
        if no.startswith(prefix):
            try:
                max_seq = max(max_seq, int(no[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{max_seq + 1:04d}"


# ── 对外的读写接口（接真 ERP 时替换这几个函数即可） ──


def append_pending_order(order: Dict[str, Any]) -> Dict[str, Any]:
    """写入待排产池。命中幂等键则直接返回已有单号，不重复写入。

    入参用英文字段名（与 workflow 槽位一致），落表时映射成中文表头。
    """
    path = _pending_path()
    with _excel_lock:
        rows = _load_rows(path, _PENDING_SHEET)
        idem = order["idempotency_key"]

        for r in rows:
            if str(r.get("幂等键") or "") == idem:
                logger.info(f"[erp] 幂等命中，返回已有单号 {r.get('单号')}")
                return {"order_no": r.get("单号"), "duplicated": True, "created_at": r.get("创建时间")}

        order_no = _next_order_no(rows)
        now = datetime.now().isoformat(timespec="seconds")
        record = [
            order_no, idem,
            order.get("product"), order.get("volume_ton"), order.get("delivery_date"),
            order.get("customer"), order.get("contact"), order.get("remark"),
            "pending", order.get("run_id", ""), now, "", "",
        ]
        rows.append({h: v for h, v in zip(PENDING_HEADERS, record)})
        _write_rows(
            path, _PENDING_SHEET, PENDING_HEADERS,
            [[r.get(h, "") for h in PENDING_HEADERS] for r in rows],
        )
        logger.info(f"[erp] 待排产池新增 {order_no}：{order.get('产品')} {order.get('数量(吨)')}吨 交期{order.get('交期')}")
        return {"order_no": order_no, "duplicated": False, "created_at": now}


def list_pending_orders(limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """拉取状态为 pending 的订单（定时排产任务用）"""
    rows = [r for r in _load_rows(_pending_path(), _PENDING_SHEET)
            if str(r.get("状态") or "") == "pending"]
    rows.sort(key=lambda r: (str(r.get("交期") or ""), str(r.get("创建时间") or "")))
    return rows[:limit] if limit else rows


def write_production_plan(plan: List[Dict[str, Any]]) -> str:
    """覆盖写入生产计划（定时排产任务用）"""
    path = _plan_path()
    with _excel_lock:
        rows = []
        for idx, item in enumerate(plan, start=1):
            rows.append([
                idx, item.get("单号"), item.get("产品"), item.get("数量(吨)"),
                item.get("交期"), item.get("客户"), item.get("排产日期"), item.get("产线"),
            ])
        _write_rows(path, _PLAN_SHEET, PLAN_HEADERS, rows)
    logger.info(f"[erp] 生产计划已生成，共 {len(plan)} 条")
    return path


def mark_scheduled(order_nos: List[str], schedule_date: str, line: str = "") -> int:
    """把订单标记为已排产并回写排产日期/产线"""
    path = _pending_path()
    with _excel_lock:
        rows = _load_rows(path, _PENDING_SHEET)
        target = set(order_nos)
        changed = 0
        for r in rows:
            if str(r.get("单号") or "") in target and str(r.get("状态") or "") == "pending":
                r["状态"] = "scheduled"
                r["排产日期"] = schedule_date
                if line:
                    r["产线"] = line
                changed += 1
        if changed:
            _write_rows(
                path, _PENDING_SHEET, PENDING_HEADERS,
                [[r.get(h, "") for h in PENDING_HEADERS] for r in rows],
            )
        return changed


def read_plan() -> List[Dict[str, Any]]:
    """读取生产计划（排产人员查看用）"""
    return _load_rows(_plan_path(), _PLAN_SHEET)


def read_pending() -> List[Dict[str, Any]]:
    """读取待排产池全部记录（含已排产的）"""
    return _load_rows(_pending_path(), _PENDING_SHEET)


def _is_feasible_delivery(target: Any, today: Optional[date] = None) -> bool:
    """交期可行性：必须能解析成日期且不早于今天"""
    from ..nodes import normalize_date

    base = today or date.today()
    normalized = normalize_date(target, base)
    if not isinstance(normalized, str) or len(normalized) != 10:
        return False
    try:
        parsed = datetime.strptime(normalized, "%Y-%m-%d").date()
    except ValueError:
        return False
    return parsed >= base