"""
LLM服务 - 封装OpenAI客户端为可调用的服务
"""

from typing import Dict, List, Optional
import logging
import base64
import openai

logger = logging.getLogger(__name__)


class LLMService:
    """LLM服务客户端"""

    def __init__(self, api_key: str, base_url: str, model_name: str = "mimo-72b", temperature: float = 0.7, max_tokens: int = 4096, timeout: float = 60.0):
        # timeout 必须显式设置：openai SDK 默认 600s，慢供应商下单请求可挂死 10 分钟
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def generate(self, prompt: str, system_prompt: Optional[str] = None, temperature: Optional[float] = None, max_tokens: Optional[int] = None) -> Dict:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        try:
            response = await self.client.chat.completions.create(
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
            response = await self.client.chat.completions.create(
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
            response = await self.client.chat.completions.create(
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

            response = await self.client.chat.completions.create(**kwargs)
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
