# 固定流程定义（YAML）

把流程写成 YAML 放这里，**重启服务自动加载**，不用改代码。

## 加一个新流程

1. 复制 `methane_order.yaml` 当模板
2. 改 `id`（唯一标识）和各步骤
3. 如果用到写库/通知，在 `app/workflows/handlers/` 加 handler 并注册
4. `GET /api/workflows/flows` 确认加载成功

## 节点类型速查

| type | 用途 | 要写代码 |
|---|---|---|
| `slot_extract` | 抽槽位，后面接 `__gate` 闸门自动追问缺失项 | 否 |
| `rule_check` | 校验，`on_fail` 指定失败跳哪 | 否 |
| `human_approval` | 人工卡点，挂起等人审批 | 否 |
| `branch` | 条件跳转 | 否 |
| `llm` | 调大模型，结果存`output_key` | 否 |
| `side_effect` | 写库、调业务系统 | **是** |
| `notify` | 发通知 | **是** |
| `end` | 结束。主链默认流向 id为 `done` 的 end | 否 |

**通用字段**（任何步骤都能加）：
- `when:` — 条件不满足就跳过这一步
- `goto:` — 执行完跳到指定步骤

## 槽位类型

`str` / `int` / `float` / `bool` / `date`

`date` 会自动把「11.30号」「下个月底」「下月15号」「明天」归一化成 `YYYY-MM-DD`，
解析不了就保留原值，由 `rule_check` 报错。

## 注意事项

- **`end` 类型的步骤不会被串进线性主链**。`goto`/`on_fail` 指向的 id 必须存在。
- **`slot_extract` 不用自己写追问逻辑**，引擎自动挂 `__gate` 闸门。多轮补充时已填的值不会丢。
- **写库 handler 必须幂等**（用 `run_id:step_id` 作键）。LangGraph 恢复时节点会重入。
- **版本管理**：改流程时 `version` +1，同`id` 多版本时 registry 取最大的。
- YAML 写错不会让服务挂掉，`load()` 只跳过该文件并打错误日志。

## 现有的流程

### methane_order（甲烷产品下单）
抽槽位 → 数量校验（≤200 吨）→ 大客户信用评估 → 写 ERP 待排产池 → 通知用户。
配套 `nightly_scheduling()` 是**定时任务**（不是 workflow），每天 20:00 拉取 pending
订单按交期排产。详见 `handlers/erp.py` 与 `handlers/order_handlers.py`。

### purchase_order（通用采购下单）
抽槽位 → 数量校验 → 主管审批 → 财务审批（≥100 时生效）→ 写库 → 通知。
handler 还是骨架（只打日志），接真 DB 时替换 `handlers/__init__.py`。