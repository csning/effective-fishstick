"""News & announcements data via AkShare EastMoney feed."""

import time
from datetime import datetime, timedelta
from typing import Optional

import akshare as ak
import pandas as pd
import requests
from loguru import logger

from config import get_settings
from .cache import DataCache

_settings = get_settings()
_cache = DataCache(_settings.data.cache_dir)


def _sleep():
    time.sleep(_settings.data.request_interval)


def get_stock_news(symbol: str, days: int = 7) -> pd.DataFrame:
    """Pull recent news for a specific stock from EastMoney.

    Args:
        symbol: e.g. "600519"
        days: how many calendar days of news to pull
    """
    cache_key = DataCache.make_key("stock_news", symbol=symbol, days=days)
    cached = _cache.get(cache_key, ttl_seconds=3600)
    if cached is not None:
        return cached

    try:
        df = ak.stock_news_em(symbol=symbol)
        _sleep()
    except Exception as e:
        logger.warning(f"News pull failed for {symbol}: {e}")
        return pd.DataFrame()

    if df is None or df.empty:
        return pd.DataFrame()

    if "发布时间" in df.columns:
        df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
        cutoff = datetime.now() - timedelta(days=days)
        df = df[df["发布时间"] >= cutoff]

    _cache.put(cache_key, df)
    logger.info(f"News: {symbol} ({len(df)} items)")
    return df


def get_announcements(symbol: str, days: int = 30) -> pd.DataFrame:
    """Pull company announcements/disclosures."""
    cache_key = DataCache.make_key("announcement", symbol=symbol, days=days)
    cached = _cache.get(cache_key, ttl_seconds=7200)
    if cached is not None:
        return cached

    try:
        df = ak.stock_zh_a_disclosure_report_cninfo(symbol=symbol)
        _sleep()
    except Exception as e:
        logger.warning(f"Announcement pull failed for {symbol}: {e}")
        return pd.DataFrame()

    return df or pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def get_market_news(days: int = 3) -> pd.DataFrame:
    """Pull market news headlines via EastMoney fast-news API.

    Uses np-weblist.eastmoney.com which returns 200 items in a single call,
    unlike the paginated stock_info_global_em which fetches 58 pages.
    """
    cache_key = DataCache.make_key("market_news", days=days)
    cached = _cache.get(cache_key, ttl_seconds=1800)
    if cached is not None:
        return cached

    try:
        url = "https://np-weblist.eastmoney.com/comm/web/getFastNewsList"
        params = {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": "200",
        }
        resp = requests.get(url, params=params, timeout=15,
                           headers={"User-Agent": "Mozilla/5.0",
                                   "Referer": "https://kuaixun.eastmoney.com/"})
        data = resp.json()
        items = data.get("data", {}).get("fastNewsList", [])
        if not items:
            return pd.DataFrame()

        rows = []
        for item in items:
            rows.append({
                "标题": item.get("title", ""),
                "摘要": item.get("summary", ""),
                "发布时间": item.get("showTime", ""),
                "链接": f"https://finance.eastmoney.com/a/{item.get('code', '')}.html",
            })
        df = pd.DataFrame(rows)
        _sleep()
    except Exception as e:
        logger.warning(f"Market news pull failed: {e}")
        return pd.DataFrame()

    if df is not None and not df.empty:
        if "发布时间" in df.columns:
            df["发布时间"] = pd.to_datetime(df["发布时间"], errors="coerce")
            cutoff = datetime.now() - timedelta(days=days)
            df = df[df["发布时间"] >= cutoff]
        _cache.put(cache_key, df)
    return df or pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    return df
    if df is None or df.empty:
        return pd.DataFrame()
    return df
