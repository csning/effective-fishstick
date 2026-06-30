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

    elif cmd == "risk":
        from engine.risk import RiskEngine
        # already imported at top
        s = get_settings()
        engine = RiskEngine(
            position_caps=s.risk.position_caps,
            stop_loss_pcts=s.risk.stop_loss_pcts,
        )
        assessment = engine.assess()
        print(f"风险等级: {assessment.level}/5")
        print(f"综合评分: {assessment.score:.3f}")
        print(f"仓位上限: {assessment.position_cap:.0%}")
        print(f"止损宽度: {assessment.stop_loss_pct:.1%}")
        print(f"分析: {assessment.reasoning}")

    elif cmd == "profile":
        from engine.profile import ProfileEngine
        engine = ProfileEngine()
        engine.load_all()
        name = sys.argv[2] if len(sys.argv) > 2 else None
        if name:
            bias = engine.apply(name)
            print(f"切换策略画像: {name}")
            print(f"  名称: {bias.name}")
            print(f"  描述: {bias.description}")
            print(f"  PE 上限: {bias.pe_max}")
            print(f"  持仓周期: {bias.holding_period}")
            print(f"  信号灵敏度: {bias.signal_sensitivity}")
            print(f"  最大持仓数: {bias.max_positions}")
        else:
            profiles = engine.list_profiles()
            print("可用策略画像:")
            for k, v in profiles.items():
                mark = " *" if k == engine.active_name else ""
                print(f"  {k}: {v}{mark}")

    elif cmd == "import":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        if not path:
            print("用法: python main.py import <csv_file>")
            print("CSV 格式: code,name,direction,price,shares,date")
            return
        from engine.trade_import import import_and_generate
        profile = import_and_generate(path)
        print(f"画像反推完成: {profile.name}")
        print(f"  描述: {profile.description}")
        print(f"  持股周期: {profile.holding_period}")
        print(f"  PE 上限: {profile.pe_max}")

    elif cmd == "anomaly":
        codes = sys.argv[2:] if len(sys.argv) > 2 else []
        from agents.base import AgentContext
        from agents.anomaly import AnomalyMonitor
        monitor = AnomalyMonitor(use_llm=False)
        ctx = AgentContext(risk_level=settings.risk.default_level)
        if codes:
            result = await monitor.run(ctx, symbols=codes)
        else:
            result = await monitor.run(ctx)
        print(result.summary)

    elif cmd == "backtest":
        codes = sys.argv[2:] if len(sys.argv) > 2 else []
        if not codes:
            print("用法: python main.py backtest CODE1 [CODE2 ...]")
            print("示例: python main.py backtest 600519 000858 300750")
            return
        from backtest.engine import BacktestEngine
        engine = BacktestEngine(
            start="2025-01-01", end="2026-06-30",
            symbols=codes, top_n=min(5, len(codes)),
        )
        result = engine.run()
        print(engine.format_result(result))

    elif cmd == "serve":
        from web.server import Server
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8000
        await Server(port=port).start()

    else:
        print(__doc__)


if __name__ == "__main__":
    asyncio.run(main())
