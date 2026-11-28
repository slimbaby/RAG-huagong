"""
下单流程的 handler（side_effect / notify）

这里是「写 ERP」和「通知用户」两件事的落点。真实接入时把 erp.py
的读写函数换成真ERP 调用，本文件不用改。
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict

from ..state import StepDef, WorkflowState
from . import erp

logger = logging.getLogger(__name__)


def _slots(state: WorkflowState) -> Dict[str, Any]:
    return state.get("slots", {}) or {}


async def write_pending_order(state: WorkflowState, step: StepDef) -> Dict[str, Any]:
    """把订单写进 ERP 待排产池（status=pending）

    幂等：run_id:step_id 作为幂等键。LangGraph 恢复时节点会重入，
    没有它会重复建单。
    """
    slots = _slots(state)
    run_id = state.get("run_id", "")
    # P0: 优先用稳定的业务幂等键（跨重试/重复提交不变）再拼 step_id；
    #     无业务键时回退 run_id:step_id（兼容旧流程实例）。
    idem_base = (state.get("metadata") or {}).get("idempotency_key")
    idempotency_key = f"{idem_base}:{step.id}" if idem_base else f"{run_id}:{step.id}"

    order = {
        "idempotency_key": idempotency_key,
        "product": slots.get("product", ""),
        "volume_ton": slots.get("volume_ton", 0),
        "delivery_date": slots.get("delivery_date", ""),
        "customer": slots.get("customer", ""),
        "contact": slots.get("contact", ""),
        "remark": slots.get("remark", ""),
        "run_id": run_id,
    }
    result = erp.append_pending_order(order)

    return {
        "order_no": result["order_no"],
        "duplicated": result["duplicated"],
        "erp_synced": True,
    }


async def notify_order_received(state: WorkflowState, step: StepDef) -> Dict[str, Any]:
    """通知用户：单据已接收，进入待排产池

    通知是 best-effort：失败不阻断流程（单子已经落库了）。
    """
    slots = _slots(state)
    results = state.get("step_results", {}) or {}
    write_res = results.get("write_pending_order", {}) or {}
    order_no = write_res.get("order_no", "")

    logger.info(
        f"[notify] 模板={step.template} 单号={order_no} "
        f"产品={slots.get('product')} 交期={slots.get('delivery_date')}"
    )

    # 真实实现示例：
    #   from ..services.notify_service import send_workflow_notification
    #   await send_workflow_notification(
    #       to=state.get("metadata", {}).get("username"),
    #       template=step.template,
    #       context={"order_no": order_no, **slots},
    #   )
    return {"sent": True, "template": step.template, "order_no": order_no}


async def notify_order_rejected(state: WorkflowState, step: StepDef) -> Dict[str, Any]:
    """校验不通过时通知用户原因"""
    slots = _slots(state)
    reason = state.get("error") or step.display_name()
    logger.info(f"[notify] 下单被拒 reason={reason} slots={slots}")
    return {"sent": True, "template": step.template, "reason": reason}


# ── 定时排产批处理（不属于 workflow） ──


def nightly_scheduling(run_date: str = "", line: str = "L1甲烷线") -> Dict[str, Any]:
    """拉取 pending 订单 → 按交期排产 → 生成生产计划

    这是**定时任务**而不是 workflow 步骤：等时间不适合挂在流程实例上
    （会让 checkpoint 里躺十几个小时）。建议每天 20:00 由 APScheduler
    或系统 cron 调用。

    真实接入时这个函数应搬到 ERP 侧或独立的排产服务。
    """
    target = run_date or date.today().isoformat()
    pending = erp.list_pending_orders()
    if not pending:
        logger.info("[排产] 待排产池为空，跳过")
        return {"scheduled": 0, "plan_path": "", "message": "待排产池为空"}

    # 按交期升序排产，同交期按数量降序（大单先排）
    ordered = sorted(
        pending,
        key=lambda r: (str(r.get("交期") or "9999-12-31"), -float(r.get("数量(吨)") or 0)),
    )

    plan = []
    scheduled_nos: list = []
    for item in ordered:
        # 交期越近越优先当天投产；远的顺延
        lead_days = 2
        try:
            due = datetime.strptime(str(item.get("交期")), "%Y-%m-%d").date()
            lead_days = max(0, (due - date.today()).days - 1)
        except (ValueError, TypeError):
            pass
        schedule_date = (date.today() + timedelta(days=min(lead_days, 30))).isoformat()
        plan.append({**item, "排产日期": schedule_date, "产线": line})
        scheduled_nos.append(str(item.get("单号") or ""))

    plan_path = erp.write_production_plan(plan)
    erp.mark_scheduled(scheduled_nos, date.today().isoformat(), line)

    logger.info(f"[排产] {len(plan)} 条订单已排产，计划写入 {plan_path}")
    return {
        "scheduled": len(plan),
        "plan_path": plan_path,
        "run_date": target,
        "first_delivery": plan[0].get("交期") if plan else None,
    }