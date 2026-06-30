"""行情数据：A 股日线、指数、板块资金流、美股指数，通过 AkShare 获取。"""

import time
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import requests
import pandas as pd
from loguru import logger

from config import get_settings
from .cache import DataCache

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "text/html,application/json,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Connection": "keep-alive",
}

# _http imports removed — using direct HTTP

_settings = get_settings()
_cache = DataCache(_settings.data.cache_dir)

# A 股指数代码
_CN_INDICES = {
    "sh000001": "上证指数", "sz399001": "深证成指",
    "sz399006": "创业板指", "sh000688": "科创50",
    "sh000300": "沪深300", "sh000016": "上证50",
    "sh000905": "中证500", "sz399673": "创业板50",
}

_US_INDICES = {
    ".DJI": "道琼斯", ".IXIC": "纳斯达克", ".SPX": "标普500",
}


def _sleep():
    time.sleep(_settings.data.request_interval)


# ---------------------------------------------------------------------------
# A 股日线
# ---------------------------------------------------------------------------

def get_daily_kline(symbol: str, days: int = 250, adjust: str = "qfq") -> pd.DataFrame:
    """拉取单只 A 股历史日线。

    Args:
        symbol: 股票代码，如 "600519"
        days: 回溯天数
        adjust: 复权方式 — "qfq"（前复权）、"hfq"（后复权）、""（不复权）
    """
    cache_key = DataCache.make_key("kline", symbol=symbol, days=days, adjust=adjust)
    cached = _cache.get(cache_key, ttl_seconds=3600 * 6)
    if cached is not None:
        return cached

    start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")

    try:
        secid = f"1.{symbol}" if symbol.startswith("6") else f"0.{symbol}"
        url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "klt": "101",
            "fqt": "1" if adjust == "qfq" else "2" if adjust == "hfq" else "0",
            "beg": start,
            "end": end,
        }
        resp = requests.get(url, params=params, headers=_EM_HEADERS, timeout=30, verify=False)
        resp.raise_for_status()
        data = resp.json()
        klines = data.get("data", {}).get("klines", [])
        if not klines:
            return pd.DataFrame()
        rows = []
        for k in klines:
            parts = k.split(",")
            rows.append({"date": parts[0], "open": parts[1], "close": parts[2], "high": parts[3], "low": parts[4], "volume": parts[5], "amount": parts[6], "turnover": parts[10] if len(parts) > 10 else "0"})
        df = pd.DataFrame(rows)
        _sleep()
    except Exception as e:
        logger.warning(f"日线拉取失败 {symbol}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df.columns = [c.lower() for c in df.columns]
    df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "换手率": "turnover",
    }, inplace=True)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    _cache.put(cache_key, df)
    logger.info(f"日线数据: {symbol} ({len(df)} 行)")
    return df


# ---------------------------------------------------------------------------
# 指数数据
# ---------------------------------------------------------------------------

def get_index_daily(code: str, days: int = 60) -> pd.DataFrame:
    """拉取 A 股或美股指数日线。"""
    cache_key = DataCache.make_key("index", code=code, days=days)
    cached = _cache.get(cache_key, ttl_seconds=3600 * 6)
    if cached is not None:
        return cached

    try:
        if code in _US_INDICES:
            secid = f"1.{code[2:]}" if code.startswith("sh") else f"0.{code[2:]}"
            url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
            params = {
                "secid": secid,
                "fields1": "f1,f2,f3,f4,f5",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "klt": "101",
                "fqt": "0",
                "beg": "19900101",
                "end": "20500101",
            }
            resp = requests.get(url, params=params, headers=_EM_HEADERS, timeout=30, verify=False)
            resp.raise_for_status()
            data = resp.json()
            klines = data.get("data", {}).get("klines", [])
            if not klines:
                return pd.DataFrame()
            rows = []
            for k in klines:
                parts = k.split(",")
                rows.append({"date": parts[0], "open": parts[1], "close": parts[2], "high": parts[3], "low": parts[4], "volume": parts[5], "amount": parts[6]})
            df = pd.DataFrame(rows)
        _sleep()
    except Exception as e:
        logger.warning(f"指数拉取失败 {code}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    df.columns = [c.lower() for c in df.columns]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    df = df.tail(days)
    df.sort_values("date", inplace=True)
    df.reset_index(drop=True, inplace=True)
    _cache.put(cache_key, df)
    return df


def get_us_market_snapshot() -> dict[str, pd.DataFrame]:
    """拉取美股三大指数最近 5 天数据。"""
    result = {}
    for code in _settings.data.us_indices:
        df = get_index_daily(code, days=5)
        if not df.empty:
            result[code] = df
    return result


def get_index_names() -> dict[str, str]:
    return {**_CN_INDICES, **_US_INDICES}


# ---------------------------------------------------------------------------
# 板块资金流 & 市场宽度
# ---------------------------------------------------------------------------

def get_sector_flow() -> pd.DataFrame:
    """东方财富行业资金流排名。"""
    cache_key = DataCache.make_key("sector_flow")
    cached = _cache.get(cache_key, ttl_seconds=3600)
    if cached is not None:
        return cached

    try:
        df = _fetch_sector_flow(indicator="今日", sector_type="行业资金流")
        _sleep()
    except Exception as e:
        logger.warning(f"板块资金流拉取失败: {e}")
        return pd.DataFrame()

    if df is not None and not df.empty:
        _cache.put(cache_key, df)
        logger.info(f"板块资金流: {len(df)} 个板块")
    return df or pd.DataFrame()


def get_market_breadth() -> dict:
    """计算市场宽度：涨跌家数比。

    使用成交额 Top 300 活跃股作为市场代表样本，
    避免全市场 5000+ 股票的慢速分页查询（~70s → ~1s）。
    这 300 只覆盖了市场绝大部分流动性。
    """
    from .fundamental import get_active_top_n

    df = get_active_top_n(n=300, sort_by="amount")
    if df.empty:
        return {}

    pct_col = "pct_chg"
    if pct_col not in df.columns:
        return {}

    pct = pd.to_numeric(df[pct_col], errors="coerce").dropna()
    if len(pct) == 0:
        return {}

    return {
        "up": int((pct > 0).sum()),
        "down": int((pct < 0).sum()),
        "flat": int((pct == 0).sum()),
        "total": len(pct),
        "up_ratio": round(float((pct > 0).mean()), 3),
        "avg_pct": round(float(pct.mean()), 2),
    }
