"""基本面数据：按活跃度/板块精准拉取 Top-N。

不再拉全市场 5000+，而是：
1. 全市场成交额 Top 200 活跃股（含 PE/PB/换手率）
2. 热门板块 Top 5，每个板块 Top 20 成分股
3. 东方财富 HTTP 直连，多 UA 重试
"""

import time
from datetime import datetime
from typing import Optional

import pandas as pd
import requests
from loguru import logger

from config import get_settings
from .cache import DataCache

_settings = get_settings()
_cache = DataCache(_settings.data.cache_dir)

_EM_BASE = "https://push2.eastmoney.com/api/qt/clist/get"
_FIELDS = "f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f14,f15,f16,f17,f18,f20,f21,f115"

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
    "Accept": "*/*",
}


def _sleep():
    time.sleep(_settings.data.request_interval)


# ===================================================================
# 核心接口 1：全市场活跃 Top-N
# ===================================================================

def get_active_top_n(n: int = 200, sort_by: str = "amount") -> pd.DataFrame:
    """拉取成交最活跃的 Top-N 只股票（含 PE/PB/换手率/涨跌幅）。

    Args:
        n: 数量（默认 200）
        sort_by: "volume"(成交量) / "amount"(成交额) / "pct_chg"(涨跌幅)
    """
    cache_key = DataCache.make_key("active_top", n=n, sort_by=sort_by)
    cached = _cache.get(cache_key, ttl_seconds=300)
    if cached is not None:
        return cached

    fid = {"volume": "f5", "amount": "f6", "pct_chg": "f3"}.get(sort_by, "f6")
    params = {
        "fid": fid, "po": 1, "pz": n, "pn": 1, "np": 1, "fltt": 2, "invt": 2,
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": _FIELDS,
    }
    df = _http_fetch(params)
    if not df.empty:
        _cache.put(cache_key, df)
        logger.info(f"活跃 Top-{n} ({sort_by}): {len(df)} 只, PE 有效 {df['pe'].notna().sum()}")
    return df


# ===================================================================
# 核心接口 2：热门板块 + 成分股
# ===================================================================

def get_hot_sectors(top_n: int = 5) -> list[str]:
    """获取当前热门板块代码（按资金流入排名）。"""
    params = {
        "fid": "f62", "po": 1, "pz": top_n, "pn": 1,
        "np": 1, "fltt": 2, "invt": 2,
        "fs": "m:90+t:2",
        "fields": "f12,f14,f3,f62,f184,f66",
    }
    df = _http_fetch(params)
    if df.empty:
        return []
    codes = df["code"].tolist()[:top_n]
    logger.info(f"热门板块 Top-{top_n}: {codes}")
    return codes


def get_sector_stocks(sector_code: str, top_n: int = 20) -> pd.DataFrame:
    """拉取某板块成分股 Top-N（按成交额排序）。"""
    cache_key = DataCache.make_key("sector", code=sector_code, n=top_n)
    cached = _cache.get(cache_key, ttl_seconds=600)
    if cached is not None:
        return cached

    params = {
        "fid": "f6", "po": 1, "pz": top_n, "pn": 1,
        "np": 1, "fltt": 2, "invt": 2,
        "fs": f"b:{sector_code}+f:!50",
        "fields": _FIELDS,
    }
    df = _http_fetch(params)
    if not df.empty:
        _cache.put(cache_key, df)
        logger.info(f"板块 {sector_code} Top-{top_n}: {len(df)} 只")
    return df


def get_sector_picks(top_sectors: int = 5, per_sector: int = 20) -> pd.DataFrame:
    """组合：热门板块各取 Top-N 成分股，去重合并。

    返回合并后的 DataFrame（code 去重，保留首次出现）。
    """
    sectors = get_hot_sectors(top_sectors)
    if not sectors:
        logger.warning("无法获取热门板块")
        return pd.DataFrame()

    frames = []
    for code in sectors:
        df = get_sector_stocks(code, per_sector)
        if not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame()

    merged = pd.concat(frames, ignore_index=True)
    merged.drop_duplicates(subset=["code"], keep="first", inplace=True)
    logger.info(f"板块合并: {len(sectors)} 个板块, {len(merged)} 只去重后")
    return merged


# ===================================================================
# 旧接口兼容
# ===================================================================

def get_market_snapshot() -> pd.DataFrame:
    """兼容旧调用：拉取成交额 Top 300 活跃股。"""
    return get_active_top_n(n=300, sort_by="amount")


# ===================================================================
# HTTP 请求核心
# ===================================================================

def _http_fetch(params: dict) -> pd.DataFrame:
    """通用东方财富 HTTP 请求 + 多 UA/SSL 重试。

    Args:
        params: URL 查询参数
    Returns:
        解析后的 DataFrame，列名为英文标准名
    """
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    ]

    for i, ua in enumerate(user_agents):
        headers = dict(_EM_HEADERS)
        headers["User-Agent"] = ua
        verify_ssl = (i == 0)

        try:
            resp = requests.get(_EM_BASE, params=params, headers=headers,
                                timeout=30, verify=verify_ssl)
            _sleep()
        except requests.exceptions.SSLError:
            logger.info(f"SSL 验证失败，跳过 (尝试 {i+1})")
            continue
        except Exception as e:
            logger.warning(f"HTTP 失败 (尝试 {i+1}): {e}")
            time.sleep(2)
            continue

        if resp.status_code != 200:
            logger.warning(f"状态码 {resp.status_code} (尝试 {i+1})")
            time.sleep(2)
            continue

        try:
            data = resp.json()
        except Exception:
            logger.warning("JSON 解析失败")
            continue

        if not data.get("data") or not data["data"].get("diff"):
            logger.warning("返回空数据")
            continue

        return _parse_items(data["data"]["diff"])

    logger.error("所有重试均失败")
    return pd.DataFrame()


def _parse_items(items: list) -> pd.DataFrame:
    """将东方财富 API 返回的原始条目转为标准 DataFrame。"""
    rows = []
    for item in items:
        rows.append({
            "code": str(item.get("f12", "")),
            "name": str(item.get("f14", "")),
            "price": item.get("f2"),
            "pct_chg": item.get("f3"),
            "chg": item.get("f4"),
            "volume": item.get("f5"),
            "amount": item.get("f6"),
            "amplitude": item.get("f7"),
            "turnover": item.get("f8"),
            "pe": item.get("f9"),
            "pb": item.get("f115") or item.get("f23"),
            "market_cap": item.get("f20"),
            "high": item.get("f15"),
            "low": item.get("f16"),
            "open": item.get("f17"),
            "pre_close": item.get("f18"),
            
        })

    df = pd.DataFrame(rows)
    # 数值列转换
    numeric_cols = ["price", "pct_chg", "volume", "amount", "amplitude",
                    "turnover", "pe", "pb", "market_cap"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

# ---------------------------------------------------------------------------
# 精选池：热门成交 + 热门板块龙头（替代全市场扫描）
# ---------------------------------------------------------------------------

_SECTOR_HOT_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f3&po=1&pz=20&pn=1&np=1&fltt=2&invt=2"
    "&fs=m:90+t:2"
    "&fields=f12,f14"
)

_STOCK_BY_SECTOR_URL = (
    "https://push2.eastmoney.com/api/qt/clist/get?"
    "fid=f62&po=0&pz=20&pn=1&np=1&fltt=2&invt=2"
    "&fs=b:{sector}+f:!50"
    "&fields=f2,f3,f4,f5,f8,f9,f12,f14,f20,f62,f115"
)


def get_curated_stocks() -> pd.DataFrame:
    """精选池：热门成交 Top 100 + 热门板块 Top 5 × 各 Top 20。

    数据源：东方财富 push2 API（已验证可用）。
    包含主力净流入字段。
    """
    cache_key = DataCache.make_key("curated")
    cached = _cache.get(cache_key, ttl_seconds=600)  # 10 分钟
    if cached is not None:
        return cached

    stocks = {}

    # --- 1. 全市场热门前 100（按成交额排） ---
    top_url = (
        "https://push2.eastmoney.com/api/qt/clist/get?"
        "fid=f6&po=1&pz=100&pn=1&np=1&fltt=2&invt=2"
        "&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
        "&fields=f2,f3,f4,f8,f9,f12,f14,f20,f62,f115,f184"
    )
    try:
        resp = requests.get(top_url, headers=_EM_HEADERS, timeout=30, verify=False)
        _sleep()
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data") and data["data"].get("diff"):
                for item in data["data"]["diff"]:
                    code = str(item.get("f12", ""))
                    if code.startswith("688") or "ST" in str(item.get("f14", "")) or str(item.get("f14", "")).startswith("N"):
                        continue
                    stocks[code] = {
                        "code": code,
                        "name": str(item.get("f14", "")),
                        "price": item.get("f2"),
                        "pct_chg": item.get("f3"),
                        "chg": item.get("f4"),
                        "turnover": item.get("f8"),
                        "pe": item.get("f9"),
                        "pb": item.get("f115"),
                        "market_cap": item.get("f20"),
                        "net_inflow": item.get("f62"),  # 主力净流入
                        "amplitude": item.get("f184"),  # 60日振幅
                    }
                logger.info(f"热门 Top 100: {len(stocks)} 只")
    except Exception as e:
        logger.warning(f"热门股票拉取失败: {e}")

    # --- 2. 热门板块 Top 5，各拉 Top 20 ---
    try:
        resp = requests.get(_SECTOR_HOT_URL, headers=_EM_HEADERS, timeout=15, verify=False)
        _sleep()
        if resp.status_code == 200:
            data = resp.json()
            if data.get("data") and data["data"].get("diff"):
                sector_codes = [
                    item.get("f12", "") for item in data["data"]["diff"][:5]
                ]
                logger.info(f"热门板块: {sector_codes}")

                for sector in sector_codes:
                    sector_url = _STOCK_BY_SECTOR_URL.replace("{sector}", sector)
                    try:
                        resp2 = requests.get(sector_url, headers=_EM_HEADERS, timeout=15, verify=False)
                        _sleep()
                        if resp2.status_code == 200:
                            data2 = resp2.json()
                            if data2.get("data") and data2["data"].get("diff"):
                                for item in data2["data"]["diff"]:
                                    code = str(item.get("f12", ""))
                                    if code in stocks:
                                        continue
                                    if code.startswith("688") or "ST" in str(item.get("f14", "")) or str(item.get("f14", "")).startswith("N"):
                                        continue
                                    stocks[code] = {
                                        "code": code,
                                        "name": str(item.get("f14", "")),
                                        "price": item.get("f2"),
                                        "pct_chg": item.get("f3"),
                                        "chg": item.get("f4"),
                                        "turnover": item.get("f8"),
                                        "pe": item.get("f9"),
                                        "pb": item.get("f115"),
                                        "market_cap": item.get("f20"),
                                        "net_inflow": item.get("f62"),
                                    }
                    except Exception as e:
                        logger.warning(f"板块 {sector} 拉取失败: {e}")
                        continue

                logger.info(f"板块精选: 总计 {len(stocks)} 只")
    except Exception as e:
        logger.warning(f"热门板块拉取失败: {e}")

    if not stocks:
        # 全失败时回退到全市场快照
        logger.warning("精选池为空，回退全市场快照")
        return get_market_snapshot()

    df = pd.DataFrame(list(stocks.values()))

    # 去重 + 数值化
    df.drop_duplicates(subset=["code"], inplace=True)
    numeric_cols = ["price", "pct_chg", "turnover", "pe", "pb", "market_cap", "net_inflow", "amplitude"]
    for c in numeric_cols:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    _cache.put(cache_key, df)
    logger.info(f"精选池: {len(df)} 只 (PE 有效: {df['pe'].notna().sum() if 'pe' in df.columns else 0})")
    return df
