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
        logger.info("选股 — 待实现")
    elif cmd == "review":
        logger.info("每日复盘 — 待实现")
    elif cmd == "serve":
        logger.info("飞书 Bot 服务 — 待实现")
    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
