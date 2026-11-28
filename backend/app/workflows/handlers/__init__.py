"""
业务侧 handler 实现

只有 side_effect 与 notify 需要写 Python，其余节点类型由引擎通用实现。
新流程接入时在这里注册 handler：

    from ..workflows.nodes import register_handler
    register_handler("purchase.write_order", write_order)
"""

import logging
from typing import Any, Dict

from ..nodes import register_handler
from ..state import StepDef, WorkflowState
from . import erp
from .order_handlers import nightly_scheduling

__all__ = ["write_order", "notify", "nightly_scheduling", "erp", "register_builtin_handlers"]

logger = logging.getLogger(__name__)


def _slots(state: WorkflowState) -> Dict[str, Any]:
    return state.get("slots", {}) or {}


async def write_order(state: WorkflowState, step: StepDef) -> Dict[str, Any]:
    """写入采购单（写公司 PostgreSQL）

    幂等要求：必须以 run_id + step_id 作为幂等键。
    恢复执行时节点可能重入，没有幂等键会重复建单。

    这里给出实现骨架：真实接入时替换为项目自己的 DAO / ORM 调用。
    """
    run_id = state.get("run_id", "")
    step_id = step.id
    # P0: 优先用稳定的业务幂等键（跨重试/重复提交不变）再拼 step_id；
    #     无业务键时回退 run_id:step_id（兼容旧流程实例）。
    idem_base = (state.get("metadata") or {}).get("idempotency_key")
    idempotency_key = f"{idem_base}:{step_id}" if idem_base else f"{run_id}:{step_id}"
    slots = _slots(state)

    quantity = slots.get("quantity", 0)
    unit_price = slots.get("unit_price", 0) or 0
    total_amount = round(float(quantity) * float(unit_price), 2)

    logger.info(f"[write_order] 幂等键={idempotency_key} 采购单金额={total_amount}")

    # 真实实现示例（按需替换）：
    #   async with db_pool.acquire() as conn:
    #       async with conn.transaction():
    #           await conn.execute(
    #               "INSERT INTO purchase_orders (idempotency_key, material_name, "
    #               "quantity, unit_price, total_amount, supplier, status) "
    #               "VALUES ($1,$2,$3,$4,$5,$6,'pending') "
    #               "ON CONFLICT (idempotency_key) DO NOTHING",
    #               idempotency_key, slots.get("material_name"), quantity,
    #               unit_price, total_amount, slots.get("supplier"),
    #           )

    return {
        "order_id": idempotency_key,
        "total_amount": total_amount,
        "material_name": slots.get("material_name", ""),
    }


async def notify(state: WorkflowState, step: StepDef) -> Dict[str, Any]:
    """通知发起人（企微 / 邮件）

    通知属于 best-effort：失败不阻断流程（业务已落地）。
    """
    slots = _slots(state)
    run_id = state.get("run_id", "")

    logger.info(
        f"[notify] 模板={step.template} run_id={run_id} "
        f"物料={slots.get('material_name', '')}"
    )

    # 真实实现示例（按需替换）：
    #   from ..services.notify_service import send_workflow_notification
    #   await send_workflow_notification(
    #       to=state.get("metadata", {}).get("username"),
    #       template=step.template,
    #       context=slots,
    #   )

    return {"sent": True, "template": step.template, "run_id": run_id}


def register_builtin_handlers() -> None:
    """注册内置 handler（应用启动时调用一次）"""
    from .order_handlers import (
        notify_order_received,
        notify_order_rejected,
        write_pending_order,
    )

    register_handler("purchase.write_order", write_order)
    register_handler("purchase.notify", notify)

    # 甲烷下单流程（Excel 模拟 ERP，接真 ERP 后替换 erp.py 实现即可）
    register_handler("erp.write_pending_order", write_pending_order)
    register_handler("erp.notify_order_received", notify_order_received)
    register_handler("erp.notify_order_rejected", notify_order_rejected)

    logger.info("内置 workflow handler 注册完成")
