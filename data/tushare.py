"""Tushare Pro 数据源 — PE/PB/市值/换手率补充。

当 AkShare 快照缺少 PE/PB 时自动补齐。
"""

import time
from datetime import datetime

import pandas as pd
import tushare as ts
from loguru import logger

from config import get_settings

_settings = get_settings()
_pro = None  # ts.pro_api instance


def _get_pro():
    global _pro
    if _pro is None:
        token = _settings.data.tushare_token
        if not token:
            logger.warning("Tushare token 未配置")
            return None
        _pro = ts.pro_api(token)
        logger.info("Tushare Pro 已连接")
    return _pro


def _sleep():
    time.sleep(_settings.data.request_interval)


def get_latest_trade_date() -> str:
    """获取最近交易日。"""
    pro = _get_pro()
    if pro is None:
        return datetime.now().strftime("%Y%m%d")
    try:
        # 用 trade_cal 获取最近交易日
        cal = pro.trade_cal(exchange="SSE", is_open="1",
                            start_date="20250101",
                            end_date=datetime.now().strftime("%Y%m%d"))
        _sleep()
        if cal is not None and not cal.empty:
            return str(cal["cal_date"].max())
    except Exception as e:
        logger.warning(f"获取交易日失败: {e}")
    return datetime.now().strftime("%Y%m%d")


def get_daily_basic_pe_pb(trade_date: str = "") -> pd.DataFrame:
    """获取全市场 PE/PB/换手率/市值。

    Args:
        trade_date: 交易日 YYYYMMDD，留空取最近交易日
    """
    pro = _get_pro()
    if pro is None:
        return pd.DataFrame()

    if not trade_date:
        trade_date = get_latest_trade_date()

    logger.info(f"Tushare daily_basic: trade_date={trade_date}")

    try:
        df = pro.daily_basic(
            trade_date=trade_date,
            fields="ts_code,trade_date,close,pe,pb,total_mv,circ_mv,turnover_rate_f"
        )
        _sleep()
    except Exception as e:
        logger.error(f"Tushare daily_basic 失败: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        logger.warning("Tushare daily_basic 返回空数据")
        return pd.DataFrame()

    # code: "600519.SH" -> "600519"
    df["code"] = df["ts_code"].str.replace(".SH", "").str.replace(".SZ", "").str.replace(".BJ", "")
    df.rename(columns={
        "close": "price_ts",
        "total_mv": "market_cap",
        "circ_mv": "float_cap",
        "turnover_rate_f": "turnover",
    }, inplace=True)

    # Keep only needed columns for merge
    keep = ["code", "pe", "pb", "market_cap", "turnover"]
    df = df[[c for c in keep if c in df.columns]]
    logger.info(f"Tushare PE/PB 拉取成功: {len(df)} 条")
    return df
