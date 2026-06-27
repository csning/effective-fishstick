#!/usr/bin/env python3
"""Effective Fishstick（有效鱼竿）入口。

用法：
    python main.py select        跑选股
    python main.py review        每日复盘
    python main.py serve         启动飞书 Bot 服务
"""

import asyncio
import sys

from config import get_settings
from loguru import logger


async def main():
    settings = get_settings()
    logger.info("Effective Fishstick（有效鱼竿）v0.1.0")
    logger.info(f"大模型: {settings.llm.provider} / {settings.llm.chat_model}")
    logger.info(f"风险等级: {settings.risk.default_level}")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"

    if cmd == "select":
        from agents.base import AgentContext
        from agents.stock_selector import StockSelector
        selector = StockSelector(top_n=20, use_llm=True)
        ctx = AgentContext(risk_level=settings.risk.default_level)
        result = await selector.run(ctx)
        print(result.summary)

    elif cmd == "review":
        logger.info("每日复盘 — 待实现")

    elif cmd == "serve":
        from web.server import Server
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        await Server(port=port).start()

    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
