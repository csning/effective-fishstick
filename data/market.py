"""行情数据：A 股日线、指数、板块、美股。

主数据源：新浪财经 JSON API（免费无认证，纯 HTTP GET，无反爬）
备数据源：Tushare Pro（免费注册 Token）
板块/活跃股：东方财富 push2 API（已验证可用）

每个函数日志标注实际数据来源：[Sina] / [Tushare] / [EastMoney]
"""

import json
import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import requests
from loguru import logger

from config import get_settings
from .cache import DataCache

_settings = get_settings()
_cache = DataCache(_settings.data.cache_dir)

# ── 代码格式 ──

_CN_INDICES = {
    "sh000001": "上证指数", "sz399001": "深证成指",
    "sz399006": "创业板指", "sh000688": "科创50",
    "sh000300": "沪深300", "sh000016": "上证50",
    "sh000905": "中证500", "sz399673": "创业板50",
}
_US_INDICES = {".DJI": "道琼斯", ".IXIC": "纳斯达克", ".SPX": "标普500"}

_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/131",
    "Referer": "https://finance.sina.com.cn/",
}

_KLINE_URL = (
    "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "CN_MarketData.getKLineData"
)

def _to_sina_code(symbol: str) -> str:
    """600519 → sh600519, sh000001 → sh000001"""
    if symbol.startswith(("sh", "sz")):
        return symbol
    if symbol.startswith(("6", "68")):
        return f"sh{symbol}"
    return f"sz{symbol}"


def _sleep():
    time.sleep(_settings.data.request_interval)


# ═══════════════════════════════════════════════════════════════════
# A 股日线 — 新浪主 + Tushare 备
# ═══════════════════════════════════════════════════════════════════

def get_daily_kline(symbol: str, days: int = 250, adjust: str = "qfq") -> pd.DataFrame:
    """拉取单只 A 股历史日线。

    数据源优先级: 新浪财经 → Tushare
    """
    cache_key = DataCache.make_key("kline", symbol=symbol, days=days, adjust=adjust)
    cached = _cache.get(cache_key, ttl_seconds=3600 * 6)
    if cached is not None:
        return cached

    # ── 新浪财经 ──
    df = _kline_from_sina(symbol, days)
    if not df.empty:
        _cache.put(cache_key, df)
        logger.info(f"日线数据: {symbol} ({len(df)} 行) [Sina]")
        return df

    # ── Tushare ──
    df = _kline_from_tushare(symbol, days)
    if not df.empty:
        _cache.put(cache_key, df)
        logger.info(f"日线数据: {symbol} ({len(df)} 行) [Tushare]")
        return df

    logger.warning(f"日线拉取失败 {symbol}: 所有数据源不可用")
    return pd.DataFrame()


def _kline_from_sina(symbol: str, days: int) -> pd.DataFrame:
    """新浪财经日线 JSON API。"""
    try:
        sina_code = _to_sina_code(symbol)
        params = {"symbol": sina_code, "scale": 240, "datalen": days + 10}
        resp = requests.get(_KLINE_URL, params=params, headers=_SINA_HEADERS, timeout=30)
        resp.raise_for_status()

        data = json.loads(resp.text)
        if not data or not isinstance(data, list):
            return pd.DataFrame()

        rows = []
        for item in data:
            rows.append({
                "date": item.get("day", ""),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume", 0)),
                "amount": 0.0,
                "turnover": 0.0,
            })

        df = pd.DataFrame(rows)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    except Exception as e:
        logger.debug(f"Sina 日线失败 {symbol}: {e}")
        return pd.DataFrame()


def _kline_from_tushare(symbol: str, days: int) -> pd.DataFrame:
    """Tushare 日线回退。"""
    try:
        from .tushare import _get_pro
        pro = _get_pro()
        if pro is None:
            return pd.DataFrame()

        ts_code = f"{symbol}.{'SH' if symbol.startswith('6') else 'SZ'}"
        start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

        df = pro.daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()

        df.columns = [c.lower() for c in df.columns]
        df.rename(columns={
            "trade_date": "date", "pre_close": "preclose",
            "pct_chg": "pct_chg", "vol": "volume",
        }, inplace=True)
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "turnover" not in df.columns:
            df["turnover"] = 0.0
        df["amount"] = df.get("amount", 0.0)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    except Exception as e:
        logger.debug(f"Tushare 日线失败 {symbol}: {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════
# 指数数据 — 新浪主 + Tushare 备
# ═══════════════════════════════════════════════════════════════════

def get_index_daily(code: str, days: int = 60) -> pd.DataFrame:
    """拉取 A 股或美股指数日线。

    数据源优先级: 新浪财经 → Tushare
    """
    cache_key = DataCache.make_key("index", code=code, days=days)
    cached = _cache.get(cache_key, ttl_seconds=3600 * 6)
    if cached is not None:
        return cached

    if code in _US_INDICES:
        return _us_index_from_tushare(code, days)

    # ── 新浪财经 ──
    df = _index_from_sina(code, days)
    if not df.empty:
        _cache.put(cache_key, df)
        return df

    # ── Tushare ──
    df = _index_from_tushare(code, days)
    if not df.empty:
        _cache.put(cache_key, df)
        return df

    logger.warning(f"指数拉取失败 {code}")
    return pd.DataFrame()


def _index_from_sina(code: str, days: int) -> pd.DataFrame:
    """新浪财经指数日线。"""
    try:
        sina_code = _to_sina_code(code)
        params = {"symbol": sina_code, "scale": 240, "datalen": days + 10}
        resp = requests.get(_KLINE_URL, params=params, headers=_SINA_HEADERS, timeout=30)
        resp.raise_for_status()

        data = json.loads(resp.text)
        if not data or not isinstance(data, list):
            return pd.DataFrame()

        rows = []
        for item in data:
            rows.append({
                "date": item.get("day", ""),
                "open": float(item.get("open", 0)),
                "high": float(item.get("high", 0)),
                "low": float(item.get("low", 0)),
                "close": float(item.get("close", 0)),
                "volume": float(item.get("volume", 0)),
                "amount": float(item.get("amount", item.get("volume", 0))),
            })

        df = pd.DataFrame(rows)
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        df = df.tail(days)
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    except Exception as e:
        logger.debug(f"Sina 指数失败 {code}: {e}")
        return pd.DataFrame()


def _index_from_tushare(code: str, days: int) -> pd.DataFrame:
    """Tushare 指数日线回退。"""
    try:
        from .tushare import _get_pro
        pro = _get_pro()
        if pro is None:
            return pd.DataFrame()

        ts_code = f"{code[2:]}.{'SH' if code.startswith('sh') else 'SZ'}"
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

        df = pro.index_daily(ts_code=ts_code, start_date=start, end_date=end)
        if df is None or df.empty:
            return pd.DataFrame()

        df.columns = [c.lower() for c in df.columns]
        df.rename(columns={"trade_date": "date"}, inplace=True)
        for c in ["open", "high", "low", "close", "volume", "amount"]:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        df = df.tail(days)
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    except Exception as e:
        logger.debug(f"Tushare 指数失败 {code}: {e}")
        return pd.DataFrame()


def _us_index_from_tushare(code: str, days: int) -> pd.DataFrame:
    """美股指数 — Tushare。"""
    try:
        from .tushare import _get_pro
        pro = _get_pro()
        if pro is None:
            return pd.DataFrame()
        ts_code = {".DJI": "DJI", ".IXIC": "IXIC", ".SPX": "SPX"}.get(code, code)
        df = pro.index_global(ts_code=ts_code, start_date="20200101")
        if df is not None and not df.empty:
            df.columns = [c.lower() for c in df.columns]
            if "trade_date" in df.columns:
                df.rename(columns={"trade_date": "date"}, inplace=True)
                df["date"] = pd.to_datetime(df["date"])
            df = df.tail(days)
            return df
    except Exception:
        pass
    return pd.DataFrame()


def get_us_market_snapshot() -> dict[str, pd.DataFrame]:
    result = {}
    for code in _settings.data.us_indices:
        df = get_index_daily(code, days=5)
        if not df.empty:
            result[code] = df
    return result


def get_index_names() -> dict[str, str]:
    return {**_CN_INDICES, **_US_INDICES}


# ═══════════════════════════════════════════════════════════════════
# 板块资金流 & 市场宽度 — 东方财富 push2（已验证可用）
# ═══════════════════════════════════════════════════════════════════

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/131",
    "Referer": "https://quote.eastmoney.com/",
}

def get_sector_flow() -> pd.DataFrame:
    cache_key = DataCache.make_key("sector_flow")
    cached = _cache.get(cache_key, ttl_seconds=3600)
    if cached is not None:
        return cached

    try:
        url = (
            "https://push2.eastmoney.com/api/qt/clist/get?"
            "fid=f62&po=1&pz=500&pn=1&np=1&fltt=2&invt=2"
            "&fs=m:90+t:2&fields=f12,f14,f3,f62,f184,f66"
        )
        resp = requests.get(url, headers=_EM_HEADERS, timeout=15, verify=False)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data", {}).get("diff", [])
        if not items:
            return pd.DataFrame()
        rows = []
        for item in items:
            rows.append({
                "序号": item.get("f12", ""), "名称": item.get("f14", ""),
                "今日涨跌幅": item.get("f3"),
                "今日主力净流入-净额": item.get("f62"),
                "今日主力净流入-净占比": item.get("f184"),
                "今日主力净流入最大股": item.get("f66", ""),
            })
        df = pd.DataFrame(rows)
        _cache.put(cache_key, df)
        logger.info(f"板块资金流: {len(df)} 个板块 [EastMoney]")
        return df
    except Exception as e:
        logger.warning(f"板块资金流拉取失败: {e}")
        return pd.DataFrame()


def get_market_breadth() -> dict:
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
