"""DeepSeek API 客户端 — OpenAI 兼容接口。

两种模式：
- chat：快速、便宜 — 用于量化筛查、新闻摘要、日常任务。
- reason：V4 Pro 深度推理 — 用于语境分析、复盘综合、黑天鹅解读。
"""

import asyncio
import time
from typing import Optional

from loguru import logger
from openai import AsyncOpenAI

from config import get_settings


class LLMClient:
    """OpenAI 兼容的 DeepSeek API 轻量封装。

    默认策略：
    - chat 走 deepseek-chat（映射到 V4 Flash），低成本高并发
    - reason 走 deepseek-v4-pro 并开启思考模式，强推理能力
    """

    def __init__(self):
        settings = get_settings()
        self._chat_client = AsyncOpenAI(
            api_key=settings.llm.api_key,
            base_url=settings.llm.base_url,
            timeout=90.0,  # Pro 思考模式响应较慢，放宽超时
        )
        self._chat_model = settings.llm.chat_model
        self._reason_model = settings.llm.reasoner_model
        self._temperature = settings.llm.temperature
        self._max_tokens = settings.llm.max_tokens
        self._last_call: float = 0
        self._min_interval: float = 1.0

    async def chat(
        self,
        prompt: str,
        system: str = "你是一位专业的量化分析师，请用中文输出，简洁具体。",
        temperature: Optional[float] = None,
    ) -> str:
        """快速对话完成（V4 Flash / standard chat）— 用于筛查、摘要等高频轻量任务。"""
        await self._rate_limit()
        try:
            resp = await self._chat_client.chat.completions.create(
                model=self._chat_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=temperature or self._temperature,
                max_tokens=self._max_tokens,
            )
            content = resp.choices[0].message.content or ""
            logger.debug(f"[chat] model={self._chat_model} tokens={resp.usage.total_tokens if resp.usage else '?'}")
            return content
        except Exception as e:
            logger.error(f"[chat] API 错误: {e}")
            return ""

    async def reason(
        self,
        prompt: str,
        system: str = "你是一位资深的 A 股投资策略师，具备深度分析能力。请用中文输出。",
    ) -> str:
        """深度推理完成（V4 Pro + 思考模式）。

        V4 Pro 的思考模式会先进行内部推理（reasoning_content），
        然后输出最终结论（content）。我们只返回 content，
        但记录 reasoning 的 token 消耗供成本分析。
        """
        await self._rate_limit()
        try:
            resp = await self._chat_client.chat.completions.create(
                model=self._reason_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=min(self._max_tokens * 2, 8192),
                extra_body={"thinking": {"type": "enabled"}},
            )
            msg = resp.choices[0].message
            content = msg.content or ""

            # V4 Pro 思考模式下返回 reasoning_content
            reasoning = getattr(msg, "reasoning_content", None)
            usage = resp.usage
            if usage:
                reasoning_tokens = getattr(usage, "reasoning_tokens", 0)
                completion_tokens = getattr(usage, "completion_tokens", 0)
                logger.info(
                    f"[reason] model={self._reason_model} "
                    f"reasoning_tokens={reasoning_tokens} "
                    f"completion_tokens={completion_tokens} "
                    f"total_tokens={usage.total_tokens}"
                )
            return content
        except Exception as e:
            logger.error(f"[reason] API 错误: {e}")
            return ""

    def chat_sync(self, prompt: str, system: str = "") -> str:
        """chat 的同步便捷包装。"""
        return asyncio.run(self.chat(prompt, system))

    def reason_sync(self, prompt: str, system: str = "") -> str:
        """reason 的同步便捷包装。"""
        return asyncio.run(self.reason(prompt, system))

    async def _rate_limit(self):
        """API 调用速率限制。"""
        elapsed = time.monotonic() - self._last_call
        if elapsed < self._min_interval:
            await asyncio.sleep(self._min_interval - elapsed)
        self._last_call = time.monotonic()


# 单例
_llm: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
