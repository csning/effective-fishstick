#!/usr/bin/env python3
"""选股全流程验证"""

import asyncio, sys, time
sys.path.insert(0, ".")

from loguru import logger
from data.fundamental import get_curated_stocks
from data.market import get_index_daily, get_market_breadth
from agents.base import AgentContext
from agents.stock_selector import StockSelector

async def main():
    # ---- 1. 精选池 ----
    t0 = time.time()
    df = get_curated_stocks()
    msg = "精选池: {} 只 ({:.1f}s)".format(len(df), time.time() - t0)
    print("\n" + "=" * 50)
    print(msg)
    for c in ["code","name","price","pe","pb","pct_chg","turnover","net_inflow"]:
        if c in df.columns:
            print("   {}: {}/{} 有效".format(c, df[c].notna().sum(), len(df)))

    # ---- 2. 选股（纯因子，不开 LLM） ----
    msg2 = "选股中（纯因子打分）..."
    print("\n" + msg2)
    t0 = time.time()
    selector = StockSelector(top_n=20, use_llm=False)
    ctx = AgentContext(risk_level=3)
    result = await selector.run(ctx)
    print("   耗时 {:.1f}s | 市场状态: {}".format(time.time()-t0, result.meta.get('regime','?')))
    print(result.summary[:800])

    # ---- 3. 选股 + LLM 深度分析 ----
    msg3 = "选股 + DeepSeek V4 Pro 深度分析..."
    print("\n" + msg3)
    selector_llm = StockSelector(top_n=10, use_llm=True)
    result2 = await selector_llm.run(ctx)
    print(result2.summary[:1500])

asyncio.run(main())
