"""
检查点管理器 - 任务生命周期管理
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import os
import uuid
import logging

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class StepCheckpoint:
    step_id: str
    step_name: str
    status: str = "pending"
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    input_data: Dict = field(default_factory=dict)
    output_data: Dict = field(default_factory=dict)
    error: str = ""
    metadata: Dict = field(default_factory=dict)


@dataclass
class TaskCheckpoint:
    task_id: str
    task_type: str
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""
    request_data: Dict = field(default_factory=dict)
    user_context: Dict = field(default_factory=dict)
    steps: List[StepCheckpoint] = field(default_factory=list)
    current_step_index: int = 0
    state_data: Dict = field(default_factory=dict)
    metadata: Dict = field(default_factory=dict)
    schema_version: int = 1


class Checkpointer:
    """检查点管理器"""

    def __init__(self, storage_path: str = "./checkpoints"):
        self.storage_path = storage_path
        self._active_tasks: Dict[str, TaskCheckpoint] = {}
        os.makedirs(storage_path, exist_ok=True)

    def create_task(self, task_type: str, request_data: Dict = None, user_context: Dict = None, task_id: str = None) -> str:
        task_id = task_id or str(uuid.uuid4())[:8]
        now = datetime.now().isoformat()
        task = TaskCheckpoint(task_id=task_id, task_type=task_type, status=TaskStatus.RUNNING.value, created_at=now, updated_at=now, request_data=request_data or {}, user_context=user_context or {})
        self._active_tasks[task_id] = task
        self._persist_task(task)
        return task_id

    def start_step(self, task_id: str, step_id: str, step_name: str, input_data: Dict = None):
        task = self._active_tasks.get(task_id)
        if not task:
            return
        step = StepCheckpoint(step_id=step_id, step_name=step_name, status=StepStatus.RUNNING.value, started_at=datetime.now().isoformat(), input_data=input_data or {})
        task.steps.append(step)
        task.current_step_index = len(task.steps) - 1
        task.updated_at = datetime.now().isoformat()

    def complete_step(self, task_id: str, step_id: str, output_data: Dict = None):
        task = self._active_tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.step_id == step_id and step.status == StepStatus.RUNNING.value:
                step.status = StepStatus.COMPLETED.value
                step.completed_at = datetime.now().isoformat()
                step.output_data = output_data or {}
                break
        task.updated_at = datetime.now().isoformat()

    def fail_step(self, task_id: str, step_id: str, error: str):
        task = self._active_tasks.get(task_id)
        if not task:
            return
        for step in task.steps:
            if step.step_id == step_id:
                step.status = StepStatus.FAILED.value
                step.completed_at = datetime.now().isoformat()
                step.error = error
                break
        task.updated_at = datetime.now().isoformat()

    def pause_task(self, task_id: str):
        task = self._active_tasks.get(task_id)
        if task:
            task.status = TaskStatus.PAUSED.value
            task.updated_at = datetime.now().isoformat()
            self._persist_task(task)

    def resume_task(self, task_id: str):
        task = self._active_tasks.get(task_id)
        if task:
            task.status = TaskStatus.RUNNING.value
            task.updated_at = datetime.now().isoformat()

    def update_state(self, task_id: str, key: str, value: Any):
        task = self._active_tasks.get(task_id)
        if task:
            task.state_data[key] = value
            task.updated_at = datetime.now().isoformat()

    def get_state(self, task_id: str, key: str) -> Any:
        task = self._active_tasks.get(task_id)
        return task.state_data.get(key) if task else None

    def complete_task(self, task_id: str, result: Any = None):
        task = self._active_tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED.value
            task.updated_at = datetime.now().isoformat()
            if result:
                task.state_data["result"] = result
            self._persist_task(task)

    def rollback_to_step(self, task_id: str, step_id: str):
        task = self._active_tasks.get(task_id)
        if not task:
            return
        for i, step in enumerate(task.steps):
            if step.step_id == step_id:
                task.steps = task.steps[:i + 1]
                task.current_step_index = i
                task.status = TaskStatus.PAUSED.value
                task.updated_at = datetime.now().isoformat()
                break
        self._persist_task(task)

    def list_tasks(self, status: str = None, task_type: str = None, limit: int = 50) -> List[TaskCheckpoint]:
        tasks = list(self._active_tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        if task_type:
            tasks = [t for t in tasks if t.task_type == task_type]
        return tasks[-limit:]

    def get_task_timeline(self, task_id: str) -> List[Dict]:
        task = self._active_tasks.get(task_id)
        if not task:
            return []
        return [{"step_id": s.step_id, "step_name": s.step_name, "status": s.status, "started_at": s.started_at, "completed_at": s.completed_at, "duration_ms": 0} for s in task.steps]

    def _persist_task(self, task: TaskCheckpoint):
        try:
            file_path = os.path.join(self.storage_path, f"task_{task.task_id}.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(task.__dict__, f, ensure_ascii=False, default=str)
        except Exception as e:
            logger.error(f"Failed to persist task: {e}")
