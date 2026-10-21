"""
Workflow 状态定义与 YAML 流程定义模型

设计要点：
- WorkflowState 独立于 RAGState，不做继承。两者生命周期完全不同：
  RAGState 是一次性问答的检索中间产物，WorkflowState 是跨请求、可挂起的长事务状态。
- 槽位（slots）是流程级"表单"，step_results 是每步产出，供后续步骤引用。
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from typing_extensions import Literal, TypedDict


# ── 节点类型 ──
NodeType = Literal[
    "slot_extract",     # LLM 抽槽位，缺必填则追问
    "rule_check",       # 纯规则校验，不调 LLM
    "branch",           # 条件跳转
    "human_approval",   # 人工卡点（interrupt 挂起）
    "llm",              # 自由生成
    "side_effect",      # 写库/调业务系统（Python handler）
    "notify",           # 发通知（Python handler）
    "end",              # 终止
]

SlotType = Literal["str", "int", "float", "bool", "date"]


class SlotDef(BaseModel):
    """流程级槽位定义"""
    name: str
    type: SlotType = "str"
    required: bool = False
    description: str = ""
    default: Any = None


class RuleDef(BaseModel):
    """单条校验规则"""
    field: str
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in", "contains"]
    value: Any = None
    error: str = ""


class ConditionDef(BaseModel):
    """条件表达式（用于 branch 跳转与步骤生效判断）"""
    field: str
    op: Literal["gt", "gte", "lt", "lte", "eq", "ne", "in", "not_in", "contains", "truthy", "falsy"]
    value: Any = None
    # 条件成立时跳向的步骤 id；配合 steps[].on_true / on_false 使用
    on_true: Optional[str] = None
    on_false: Optional[str] = None


class StepDef(BaseModel):
    """流程步骤定义"""
    id: str
    type: NodeType
    name: str = ""

    # slot_extract
    prompt: str = ""
    source_kb: bool = False

    # rule_check
    rules: List[RuleDef] = Field(default_factory=list)
    on_fail: Optional[str] = None

    # branch
    condition: Optional[ConditionDef] = None

    # human_approval
    approver_role: str = ""
    approver_hint: str = ""
    timeout_hours: int = 72

    # llm
    output_key: str = ""
    system_prompt: str = ""

    # side_effect / notify
    handler: str = ""

    # notify
    template: str = ""

    # 通用：跳转到指定步骤（跳过本步）
    goto: Optional[str] = None

    # 通用：本步骤是否需要执行（条件满足才执行）
    when: Optional[ConditionDef] = None

    def display_name(self) -> str:
        return self.name or self.id


class FlowDef(BaseModel):
    """一份 YAML 流程定义"""
    id: str
    version: int = 1
    name: str = ""
    description: str = ""
    slots: List[SlotDef] = Field(default_factory=list)
    steps: List[StepDef] = Field(default_factory=list)

    def get_step(self, step_id: str) -> Optional[StepDef]:
        for s in self.steps:
            if s.id == step_id:
                return s
        return None

    def first_step_id(self) -> Optional[str]:
        return self.steps[0].id if self.steps else None


class WorkflowState(TypedDict, total=False):
    """Workflow 执行状态

    注意：不继承 RAGState —— 检索问答字段（query_vector/filtered_results/citations）
    在流程里生命周期不同，混入会导致 checkpoint 体积膨胀与语义污染。
    """
    # ── 标识 ──
    run_id: str
    flow_id: str
    flow_version: int
    # ── 槽位（流程表单）──
    slots: Dict[str, Any]
    # ── 每步产出，供后续步骤引用 ──
    step_results: Dict[str, Any]
    # ── 执行位置 ──
    current_step: str
    # ── 动态跳转目标（由节点写入，条件边读取后被下一节点覆盖清空）──
    goto: Optional[str]
    # ── 闸门节点标记：合并后仍有缺失槽位，需再问一次 ──
    _gate_retry: bool
    # ── 人工卡点挂起信息 ──
    pending_approval: Optional[Dict[str, Any]]
    # ── 终止信息 ──
    status: str
    error: Optional[str]
    # ── 元信息 ──
    metadata: Dict[str, Any]
    # ── 用户输入（追问时回填）──
    user_input: Optional[str]
    # ── 待确认问题（槽位缺失时返回给前端）──
    pending_question: Optional[str]
