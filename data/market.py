"""行情数据：A 股日线、指数、板块资金流、美股指数。

主数据源：BaoStock（A 股日线/指数，免费无认证，无反爬问题）
辅助源：Tushare Pro（分钟线/财务），东方财富 push2（板块资金流/活跃股）
"""

import time
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from config import get_settings
from .cache import DataCache

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
# 代码转换
# ---------------------------------------------------------------------------

def _to_bs_code(symbol: str) -> str:
    """将项目内部代码格式转为 BaoStock 格式。

    600519  → sh.600519
    300099  → sz.300099
    sh000001 → sh.000001
    sz399006 → sz.399006
    """
    if len(symbol) >= 8 and symbol[:2] in ("sh", "sz"):
        return f"{symbol[:2]}.{symbol[2:]}"
    if symbol.startswith(("6", "68")):
        return f"sh.{symbol}"
    return f"sz.{symbol}"


def _from_bs_code(bs_code: str) -> str:
    """BaoStock 格式转回项目格式: sh.600519 → 600519"""
    return bs_code.replace("sh.", "").replace("sz.", "")


# ---------------------------------------------------------------------------
# A 股日线 (BaoStock)
# ---------------------------------------------------------------------------

def get_daily_kline(symbol: str, days: int = 250, adjust: str = "qfq") -> pd.DataFrame:
    """拉取单只 A 股历史日线。

    Args:
        symbol: 股票代码，如 "600519"
        days: 回溯天数
        adjust: 复权方式 — "qfq"（前复权）/"hfq"（后复权）/""（不复权）
    """
    cache_key = DataCache.make_key("kline", symbol=symbol, days=days, adjust=adjust)
    cached = _cache.get(cache_key, ttl_seconds=3600 * 6)
    if cached is not None:
        return cached

    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end = datetime.now().strftime("%Y-%m-%d")

    adj_map = {"qfq": "2", "hfq": "1", "": "3"}
    adj_flag = adj_map.get(adjust, "2")

    try:
        import baostock as bs
        bs.login()
        bs_code = _to_bs_code(symbol)
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,code,open,high,low,close,preclose,volume,amount,turn,tradestatus",
            start_date=start, end_date=end,
            frequency="d", adjustflag=adj_flag,
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if not rows:
            logger.warning(f"BaoStock 无数据: {symbol}")
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=[
            "date", "code", "open", "high", "low", "close",
            "preclose", "volume", "amount", "turnover", "tradestatus",
        ])
        df = df[df["tradestatus"] == "1"]  # 只保留交易日
        df = df.drop(columns=["code", "tradestatus"])

        numeric_cols = ["open", "high", "low", "close", "preclose", "volume", "amount", "turnover"]
        for c in numeric_cols:
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce")

        df["date"] = pd.to_datetime(df["date"])
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        _cache.put(cache_key, df)
        logger.info(f"日线数据: {symbol} ({len(df)} 行) [BaoStock]")
        return df

    except Exception as e:
        logger.warning(f"BaoStock 日线拉取失败 {symbol}: {e}")
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# 指数数据 (BaoStock)
# ---------------------------------------------------------------------------

def get_index_daily(code: str, days: int = 60) -> pd.DataFrame:
    """拉取 A 股指数日线。

    BaoStock 索引代码格式: sh.000001, sz.399006
    """
    cache_key = DataCache.make_key("index", code=code, days=days)
    cached = _cache.get(cache_key, ttl_seconds=3600 * 6)
    if cached is not None:
        return cached

    # 美股走 Tushare / 备用路径
    if code in _US_INDICES:
        return _get_us_index(code, days)

    try:
        import baostock as bs
        bs.login()
        bs_code = _to_bs_code(code)
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume,amount",
            start_date=(datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d"),
            end_date=datetime.now().strftime("%Y-%m-%d"),
            frequency="d", adjustflag="3",
        )
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        bs.logout()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=[
            "date", "open", "high", "low", "close", "volume", "amount",
        ])
        numeric_cols = ["open", "high", "low", "close", "volume", "amount"]
        for c in numeric_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce")

        df["date"] = pd.to_datetime(df["date"])
        df = df.tail(days)
        df.sort_values("date", inplace=True)
        df.reset_index(drop=True, inplace=True)
        _cache.put(cache_key, df)
        return df

    except Exception as e:
        logger.warning(f"BaoStock 指数拉取失败 {code}: {e}")
        return pd.DataFrame()


def _get_us_index(code: str, days: int) -> pd.DataFrame:
    """美股指数 — 尝试 Tushare，失败返回空。"""
    try:
        from .tushare import _get_pro
        pro = _get_pro()
        if pro is None:
            return pd.DataFrame()
        ts_code = {"DJI": "DJI", "IXIC": "IXIC", "SPX": "SPX"}.get(code, code)
        df = pro.index_global(ts_code=ts_code, start_date="20200101")
        if df is not None and not df.empty and len(df) > 0:
            df.columns = [c.lower() for c in df.columns]
            rename_map = {"trade_date": "date", "pct_chg": "pct_chg"}
            df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns}, inplace=True)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"])
            df = df.tail(days)
            return df
    except Exception:
        pass
    return pd.DataFrame()


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
# 板块资金流 & 市场宽度 (东方财富 push2 — 已验证可用)
# ---------------------------------------------------------------------------

_SECTOR_HOT_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f62&po=1&pz=500&pn=1&np=1&fltt=2&invt=2"
    "&fs=m:90+t:2"
    "&fields=f12,f14,f3,f62,f184,f66"
)

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 Chrome/131",
    "Referer": "https://quote.eastmoney.com/",
}

def get_sector_flow() -> pd.DataFrame:
    """东方财富行业资金流排名 — 直接 HTTP，已验证可用。"""
    cache_key = DataCache.make_key("sector_flow")
    cached = _cache.get(cache_key, ttl_seconds=3600)
    if cached is not None:
        return cached

    try:
        import requests as _req
        resp = _req.get(_SECTOR_HOT_URL, headers=_EM_HEADERS, timeout=15, verify=False)
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
        logger.info(f"板块资金流: {len(df)} 个板块")
        return df
    except Exception as e:
        logger.warning(f"板块资金流拉取失败: {e}")
        return pd.DataFrame()


def get_market_breadth() -> dict:
    """计算市场宽度：涨跌家数比（Top 300 活跃股为代表样本）。"""
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
