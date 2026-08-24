"""
韧性保护配置 - 熔断、超时、迭代限制等
"""

from typing import List, Dict, Optional
from pydantic import BaseModel, Field
import yaml
from pathlib import Path


class IterationLimits(BaseModel):
    """迭代限制配置"""
    agent_max_iterations: int = Field(default=10, description="Agent最大迭代次数")
    rag_max_retries: int = Field(default=3, description="RAG查询最大重试次数")
    llm_max_retries: int = Field(default=3, description="LLM调用最大重试次数")
    tool_max_retries: int = Field(default=2, description="工具调用最大重试次数")


class TimeoutConfig(BaseModel):
    """超时配置（秒）"""
    llm_call: int = Field(default=30, description="单次LLM调用超时")
    vector_search: int = Field(default=10, description="单次向量检索超时")
    bm25_search: int = Field(default=5, description="单次BM25检索超时")
    rag_query_total: int = Field(default=120, description="完整RAG查询超时")
    file_parse_per_mb: int = Field(default=30, description="文件解析超时（每MB）")
    web_crawl: int = Field(default=60, description="网页爬取超时")
    agent_step: int = Field(default=60, description="Agent单步执行超时")
    agent_total: int = Field(default=300, description="Agent总执行超时")


class CircuitBreakerConfig(BaseModel):
    """熔断器配置"""
    enabled: bool = Field(default=True, description="是否启用熔断器")
    failure_threshold: int = Field(default=5, description="失败次数阈值（触发熔断）")
    recovery_timeout: int = Field(default=60, description="熔断恢复时间（秒）")
    half_open_max_requests: int = Field(default=3, description="半开状态下的试探请求数")
    statistics_window: int = Field(default=300, description="统计窗口时间（秒）")


class FallbackConfig(BaseModel):
    """降级策略配置"""
    llm_unavailable: str = Field(
        default="抱歉，AI服务暂时不可用，请稍后再试。",
        description="LLM不可用时的降级响应"
    )
    retrieval_timeout: str = Field(
        default="知识库检索超时，请尝试简化您的问题。",
        description="检索超时时的降级响应"
    )
    iteration_limit_exceeded: str = Field(
        default="您的问题较为复杂，系统处理超时。请尝试将问题拆分为多个小问题。",
        description="超出迭代限制时的降级响应"
    )
    enable_faq_fallback: bool = Field(default=True, description="是否启用FAQ缓存降级")
    enable_rule_fallback: bool = Field(default=True, description="是否启用规则引擎降级")


class NotificationChannel(BaseModel):
    """通知渠道配置"""
    type: str = Field(description="通知类型 (webhook/email/sms)")
    url: Optional[str] = Field(default=None, description="Webhook URL")
    recipients: Optional[List[str]] = Field(default=None, description="邮件接收者")


class ErrorLoggingConfig(BaseModel):
    """错误记录配置"""
    enabled: bool = Field(default=True, description="是否启用详细错误记录")
    storage_path: str = Field(default="./logs/errors", description="错误日志存储路径")
    retention_days: int = Field(default=90, description="错误记录保留天数")
    log_request_body: bool = Field(default=True, description="是否记录请求体")
    log_response_body: bool = Field(default=True, description="是否记录响应体")
    max_body_length: int = Field(default=10000, description="请求体最大记录长度")
    notification_enabled: bool = Field(default=True, description="是否发送错误通知")
    notification_threshold: int = Field(default=3, description="通知阈值（连续错误次数）")
    notification_channels: List[NotificationChannel] = Field(
        default_factory=list,
        description="通知渠道列表"
    )


class RetryConfig(BaseModel):
    """重试策略配置"""
    delay_strategy: str = Field(default="exponential", description="重试延迟策略 (fixed/exponential/linear)")
    base_delay: float = Field(default=1.0, description="基础延迟（秒）")
    max_delay: float = Field(default=30.0, description="最大延迟（秒）")
    backoff_factor: float = Field(default=2.0, description="退避因子")
    jitter: bool = Field(default=True, description="是否添加随机抖动")
    retryable_exceptions: List[str] = Field(
        default=["TimeoutError", "ConnectionError", "RateLimitError", "ServiceUnavailableError"],
        description="可重试的异常类型"
    )


class ResilienceConfig(BaseModel):
    """韧性保护完整配置"""
    iteration_limits: IterationLimits = Field(default_factory=IterationLimits)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
    error_logging: ErrorLoggingConfig = Field(default_factory=ErrorLoggingConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)

    @classmethod
    def load_from_yaml(cls, config_path: str = "config/resilience.yaml") -> "ResilienceConfig":
        """从YAML文件加载配置"""
        path = Path(config_path)
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return cls(**data)
        return cls()

    def update(self, **kwargs) -> "ResilienceConfig":
        """动态更新配置"""
        current = self.model_dump()

        for key, value in kwargs.items():
            if "." in key:
                parts = key.split(".")
                target = current
                for part in parts[:-1]:
                    if part not in target:
                        target[part] = {}
                    target = target[part]
                target[parts[-1]] = value
            else:
                current[key] = value

        return ResilienceConfig(**current)

    def save_to_yaml(self, config_path: str = "config/resilience.yaml"):
        """保存配置到YAML文件"""
        path = Path(config_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                self.model_dump(),
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False
            )


# 全局韧性配置
resilience_config = ResilienceConfig.load_from_yaml()
