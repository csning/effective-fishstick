#!/usr/bin/env python3
"""Effective Fishstick（有效鱼竿）入口。

用法：
    python main.py select        跑选股
    python main.py timing CODE   技术面择时分析
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
        from agents.base import AgentContext
        from agents.reviewer import DailyReviewer
        reviewer = DailyReviewer(use_llm=True)
        ctx = AgentContext(risk_level=settings.risk.default_level)
        result = await reviewer.run(ctx)
        print(result.summary)

    elif cmd == "timing":
        codes = sys.argv[2:] if len(sys.argv) > 2 else []
        if not codes:
            print("用法: python main.py timing CODE1 [CODE2 ...]")
            print("示例: python main.py timing 600519 000858")
            return
        from agents.base import AgentContext
        from agents.timing import TimingAgent
        timing = TimingAgent(days=120, use_llm=True)
        ctx = AgentContext(risk_level=settings.risk.default_level)
        result = await timing.run(ctx, symbols=codes)
        print(result.summary)

    elif cmd == "position":
        print("持仓诊断 — 从飞书发送「持仓」指令使用，或通过代码传入持仓数据。")

    elif cmd == "serve":
        from web.server import Server
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        await Server(port=port).start()

    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
