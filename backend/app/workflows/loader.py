"""
YAML 流程定义加载器

- 启动时扫描 definitions/ 目录，加载全部 YAML 为 FlowDef
- 按 flow_id 索引，提供版本管理（同一 id 多版本时取 version 最大）
- 加载失败只跳过该文件并记录日志，不影响其他流程
"""

import logging
import os
import threading
from typing import Dict, List, Optional

import yaml

from .state import FlowDef

logger = logging.getLogger(__name__)

DEFAULT_DEFINITIONS_DIR = os.path.join(os.path.dirname(__file__), "definitions")


class FlowRegistry:
    """流程定义注册表（进程内单例，线程安全）"""

    def __init__(self, definitions_dir: Optional[str] = None):
        if not definitions_dir:
            try:
                from ..core.config import settings
                definitions_dir = settings.WORKFLOW_DEFINITIONS_DIR or None
            except Exception:
                definitions_dir = None
        self.definitions_dir = definitions_dir or DEFAULT_DEFINITIONS_DIR
        # flow_id -> {version: FlowDef}
        self._flows: Dict[str, Dict[int, FlowDef]] = {}
        self._lock = threading.Lock()
        self._loaded = False

    def load(self, force: bool = False) -> int:
        """加载全部 YAML 定义，返回成功加载的流程数量"""
        with self._lock:
            if self._loaded and not force:
                return len(self._flows)
            self._flows = {}
            if not os.path.isdir(self.definitions_dir):
                logger.warning(f"流程定义目录不存在: {self.definitions_dir}")
                self._loaded = True
                return 0

            count = 0
            for filename in sorted(os.listdir(self.definitions_dir)):
                if not (filename.endswith(".yaml") or filename.endswith(".yml")):
                    continue
                path = os.path.join(self.definitions_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        raw = yaml.safe_load(f)
                    if not raw:
                        continue
                    flow = FlowDef(**raw)
                    if not flow.id:
                        logger.warning(f"流程定义缺少 id，跳过: {filename}")
                        continue
                    self._flows.setdefault(flow.id, {})[flow.version] = flow
                    count += 1
                except Exception as e:
                    logger.error(f"加载流程定义失败 {filename}: {e}")
                    continue

            self._loaded = True
            logger.info(f"流程定义加载完成: {count} 个定义, {len(self._flows)} 个流程")
            return count

    def get(self, flow_id: str, version: Optional[int] = None) -> Optional[FlowDef]:
        """获取流程定义；version 为 None 时取最新版"""
        if not self._loaded:
            self.load()
        versions = self._flows.get(flow_id)
        if not versions:
            return None
        if version is not None:
            return versions.get(version)
        return versions[max(versions.keys())]

    def list_flows(self) -> List[Dict]:
        """列出所有流程（取最新版），供前端流程选择器使用"""
        if not self._loaded:
            self.load()
        result = []
        for flow_id, versions in self._flows.items():
            flow = versions[max(versions.keys())]
            result.append({
                "id": flow.id,
                "name": flow.name or flow.id,
                "description": flow.description,
                "version": flow.version,
                "available_versions": sorted(versions.keys()),
                "step_count": len(flow.steps),
                "slots": [
                    {"name": s.name, "type": s.type, "required": s.required, "description": s.description}
                    for s in flow.slots
                ],
                "requires_approval": any(s.type == "human_approval" for s in flow.steps),
            })
        return sorted(result, key=lambda x: x["id"])


_registry: Optional[FlowRegistry] = None
_registry_lock = threading.Lock()


def get_flow_registry(definitions_dir: Optional[str] = None) -> FlowRegistry:
    """获取全局流程注册表单例"""
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = FlowRegistry(definitions_dir)
            _registry.load()
        return _registry
