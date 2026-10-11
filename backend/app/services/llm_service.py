"""
LLM服务 - 封装OpenAI客户端为可调用的服务
"""

from typing import Dict, List, Optional
import logging
import base64
import openai

logger = logging.getLogger(__name__)


# 瞬态失败（值得重试 + 计入熔断）的 openai 异常类型：
# 超时 / 连接故障 / 429 限流 / 5xx。业务性错误（401、400、解析失败等）
# 不重试不计熔断——重试不会让它们成功，只会放大故障
_RETRYABLE_LLM_ERRORS = (
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)


class LLMService:
    """LLM服务客户端"""

    def __init__(self, api_key: str, base_url: str, model_name: str = "mimo-72b", temperature: float = 0.7, max_tokens: int = 4096, timeout: float = 60.0):
        # timeout 必须显式设置：openai SDK 默认 600s，慢供应商下单请求可挂死 10 分钟
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def _create_completion(self, **kwargs):
        """所有 completions 调用的唯一入口：韧性执行器包裹。

        - 熔断器 "llm"：连续瞬态失败 5 次后 OPEN，快速失败防止级联
          （上层节点已有降级路径：查询优化回退原句、生成节点返回错误答案）；
        - 重试：仅瞬态异常，指数退避 + 抖动，总尝试次数 = llm_max_retries（默认 3）；
        - 超时：由 openai SDK 的显式 timeout 承担，执行器不叠加
          （asyncio.wait_for 掐断 SDK 调用会留下悬挂连接，得不偿失）。
        """
        from ..resilience.executor import get_executor
        from ..core.resilience_config import resilience_config
        return await get_executor().execute_async(
            "llm",
            lambda: self.client.chat.completions.create(**kwargs),
            max_attempts=resilience_config.iteration_limits.llm_max_retries,
            retryable=_RETRYABLE_LLM_ERRORS,
        )

    async def generate(self, prompt: str, system_prompt: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> Dict:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await self._create_completion(
                model=self.model_name, messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
            )
            usage = response.usage
            return {
                "text": response.choices[0].message.content,
                "usage": {
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                }
            }
        except Exception as e:
            logger.error(f"LLM生成失败: {e}")
            raise

    async def chat(self, messages: List[dict], temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> Dict:
        try:
            response = await self._create_completion(
                model=self.model_name, messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
            )
            usage = response.usage
            return {
                "text": response.choices[0].message.content,
                "usage": {
                    "prompt_tokens": usage.prompt_tokens if usage else 0,
                    "completion_tokens": usage.completion_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                }
            }
        except Exception as e:
            logger.error(f"LLM对话失败: {e}")
            raise

    async def generate_with_image(self, prompt: str, image_data: bytes, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> str:
        image_b64 = base64.b64encode(image_data).decode("utf-8")
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
        ]}]
        try:
            response = await self._create_completion(
                model=self.model_name, messages=messages,
                temperature=temperature or self.temperature,
                max_tokens=max_tokens or self.max_tokens,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"多模态LLM图片分析失败: {e}")
            raise

    async def chat_with_tools(self, messages: List[dict], tools: Optional[List[dict]] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> Dict:
        """带工具调用的对话，支持function calling"""
        try:
            kwargs = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature or self.temperature,
                "max_tokens": max_tokens or self.max_tokens,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = await self._create_completion(**kwargs)
            message = response.choices[0].message

            result = {
                "text": message.content or "",
                "tool_calls": [],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                }
            }

            if message.tool_calls:
                for tc in message.tool_calls:
                    result["tool_calls"].append({
                        "id": tc.id,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        }
                    })

            return result
        except Exception as e:
            logger.error(f"LLM工具调用失败: {e}")
            raise
