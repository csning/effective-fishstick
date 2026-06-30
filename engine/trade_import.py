"""交易记录导入 — CSV 解析 + 行为偏差反推策略画像。

CSV 格式（最少列）：
    code, name, direction, price, shares, date
    600519, 贵州茅台, buy, 1800.00, 100, 2025-03-15
    600519, 贵州茅台, sell, 1950.00, 50, 2025-06-20

可选列: sector, pnl, reason

功能：
1. 解析 CSV 为交易记录列表
2. 统计行为特征（持股周期、行业偏好、止盈止损习惯）
3. 反推策略画像 ProfileBias
4. 导出为 profiles/ 下的 YAML 文件
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from loguru import logger

from .profile import ProfileBias


# ── 数据模型 ──

@dataclass
class TradeRecord:
    code: str
    name: str
    direction: str          # buy / sell
    price: float
    shares: int
    date: str               # YYYY-MM-DD
    sector: str = ""
    pnl: Optional[float] = None
    reason: str = ""


@dataclass
class TradeStats:
    """从交易记录中提取的行为统计。"""
    total_trades: int = 0
    buy_count: int = 0
    sell_count: int = 0
    avg_holding_days: float = 0
    median_holding_days: float = 0
    avg_pnl_pct: float = 0
    win_rate: float = 0
    avg_pe_at_buy: float = 0
    top_sectors: list = field(default_factory=list)
    most_traded: list = field(default_factory=list)
    avg_position_size: float = 0


# ── CSV 解析 ──

def parse_trades_csv(filepath: str) -> list[TradeRecord]:
    """解析交易记录 CSV 文件。

    列名不区分大小写，自动映射中英文列名。
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {filepath}")

    # 列名映射
    COL_MAP = {
        "code": "code", "代码": "code", "股票代码": "code",
        "name": "name", "名称": "name", "股票名称": "name",
        "direction": "direction", "方向": "direction", "买卖": "direction",
        "操作": "direction", "类型": "direction",
        "price": "price", "价格": "price", "成交价": "price",
        "shares": "shares", "数量": "shares", "股数": "shares",
        "date": "date", "日期": "date", "交易日期": "date",
        "sector": "sector", "行业": "sector", "板块": "sector",
        "pnl": "pnl", "盈亏": "pnl",
        "reason": "reason", "理由": "reason", "备注": "reason",
    }

    records = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV 文件为空")

        # 构建列名映射
        mapping = {}
        for col in reader.fieldnames:
            key = col.strip()
            mapped = COL_MAP.get(key, key.lower().replace(" ", "_"))
            mapping[key] = mapped

        for row in reader:
            try:
                r = {mapping.get(k, k): v.strip() if v else "" for k, v in row.items()}
                d = r.get("direction", "").lower()

                # 归一化买卖方向
                if d in ("买", "买入", "buy", "b", "long"):
                    direction = "buy"
                elif d in ("卖", "卖出", "sell", "s", "short"):
                    direction = "sell"
                else:
                    logger.warning(f"未知方向: {d}, 跳过")
                    continue

                record = TradeRecord(
                    code=str(r.get("code", "")).zfill(6),
                    name=str(r.get("name", "")),
                    direction=direction,
                    price=float(r.get("price", 0)),
                    shares=int(float(r.get("shares", 0))),
                    date=str(r.get("date", "")),
                    sector=str(r.get("sector", "")),
                    pnl=float(r["pnl"]) if r.get("pnl") else None,
                    reason=str(r.get("reason", "")),
                )
                records.append(record)
            except (ValueError, KeyError) as e:
                logger.warning(f"行解析失败: {e} | row={row}")
                continue

    logger.info(f"解析完成: {len(records)} 条交易记录")
    return records


# ── 行为统计 ──

def compute_trade_stats(records: list[TradeRecord]) -> TradeStats:
    """从交易记录计算行为统计特征。"""
    if not records:
        return TradeStats()

    records = sorted(records, key=lambda r: r.date)
    stats = TradeStats()
    stats.total_trades = len(records)

    buys = [r for r in records if r.direction == "buy"]
    sells = [r for r in records if r.direction == "sell"]
    stats.buy_count = len(buys)
    stats.sell_count = len(sells)

    # 持股周期
    holding_days = []
    pnl_pcts = []
    buy_prices: dict[str, list[tuple[str, float]]] = {}

    for r in buys:
        if r.code not in buy_prices:
            buy_prices[r.code] = []
        buy_prices[r.code].append((r.date, r.price))

    for r in sells:
        if r.code in buy_prices and buy_prices[r.code]:
            # FIFO 匹配
            buy_date_str, buy_price = buy_prices[r.code].pop(0)
            try:
                buy_date = datetime.strptime(buy_date_str, "%Y-%m-%d")
                sell_date = datetime.strptime(r.date, "%Y-%m-%d")
                days = (sell_date - buy_date).days
                if 0 <= days <= 3650:  # 过滤异常值
                    holding_days.append(days)
            except ValueError:
                pass

            if r.pnl is not None:
                pnl_pcts.append(r.pnl)
            elif buy_price > 0:
                pnl_pcts.append((r.price - buy_price) / buy_price)

    if holding_days:
        stats.avg_holding_days = round(sum(holding_days) / len(holding_days), 1)
        sorted_days = sorted(holding_days)
        mid = len(sorted_days) // 2
        stats.median_holding_days = sorted_days[mid]

    if pnl_pcts:
        stats.avg_pnl_pct = round(sum(pnl_pcts) / len(pnl_pcts), 4)
        stats.win_rate = round(sum(1 for p in pnl_pcts if p > 0) / len(pnl_pcts), 3)

    # 行业偏好
    sector_counts = {}
    for r in records:
        if r.sector:
            sector_counts[r.sector] = sector_counts.get(r.sector, 0) + 1
    stats.top_sectors = sorted(sector_counts.items(), key=lambda x: -x[1])[:5]

    # 交易频率 Top
    code_counts = {}
    for r in records:
        code_counts[r.code] = code_counts.get(r.code, 0) + 1
    stats.most_traded = sorted(code_counts.items(), key=lambda x: -x[1])[:5]

    # 平均仓位
    amounts = [r.price * r.shares for r in records if r.price > 0]
    if amounts:
        stats.avg_position_size = round(sum(amounts) / len(amounts), 2)

    return stats


# ── 画像反推 ──

def stats_to_profile(stats: TradeStats, name: str = "imported") -> ProfileBias:
    """从行为统计反推策略画像参数。"""
    profile = ProfileBias(
        name=f"Imported: {name}",
        description=f"从 {stats.total_trades} 笔交易反推",
    )

    # 持股周期 → 画像类型推断
    if stats.avg_holding_days > 60:
        profile.holding_period = "long"
        profile.signal_sensitivity = "low"
        profile.max_positions = 10
        profile.rebalance_frequency = "monthly"
        profile.pe_max = 20
    elif stats.avg_holding_days > 20:
        profile.holding_period = "medium"
        profile.signal_sensitivity = "medium"
        profile.max_positions = 15
        profile.rebalance_frequency = "weekly"
        profile.pe_max = 60
    else:
        profile.holding_period = "short"
        profile.signal_sensitivity = "high"
        profile.max_positions = 25
        profile.rebalance_frequency = "daily"
        profile.pe_max = 200
        profile.prefer_momentum = True

    # 胜率
    if stats.win_rate > 0.6:
        profile.trailing_stop = True

    # 行业偏好
    if stats.top_sectors:
        profile.prefer_sectors = [s[0] for s in stats.top_sectors[:3]]

    # 止损习惯推断
    if stats.avg_pnl_pct < -0.05:
        profile.stop_loss_pct_override = 0.05
    elif stats.avg_pnl_pct < -0.03:
        profile.stop_loss_pct_override = 0.08

    return profile


# ── 导出画像 YAML ──

def export_profile(profile: ProfileBias, output_dir: str = "profiles") -> str:
    """将 ProfileBias 导出为 YAML 文件。

    Returns: 输出文件路径
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 生成安全的文件名
    safe_name = profile.name.lower().replace(" ", "_").replace(":", "").replace("/", "_")
    filepath = out_dir / f"{safe_name}.yaml"

    data = {
        "name": profile.name,
        "description": profile.description,
        "bias": {
            "stock_selection": {
                "pe_max": profile.pe_max,
                "roe_min": profile.roe_min,
                "prefer_sectors": profile.prefer_sectors,
                "prefer_momentum": profile.prefer_momentum,
                "holding_period": profile.holding_period,
            },
            "timing": {
                "signal_sensitivity": profile.signal_sensitivity,
                "require_volume_confirmation": profile.require_volume_confirmation,
                "trailing_stop": profile.trailing_stop,
            },
            "position": {
                "concentration": profile.concentration,
                "max_positions": profile.max_positions,
                "rebalance_frequency": profile.rebalance_frequency,
            },
        },
    }

    if profile.stop_loss_pct_override is not None:
        data["bias"]["timing"]["stop_loss_pct_override"] = profile.stop_loss_pct_override

    with open(filepath, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    logger.info(f"策略画像已导出: {filepath}")
    return str(filepath)


# ── 一键导入管道 ──

def import_and_generate(filepath: str, output_dir: str = "profiles") -> ProfileBias:
    """一键：解析 CSV → 统计 → 反推画像 → 导出 YAML。"""
    records = parse_trades_csv(filepath)
    stats = compute_trade_stats(records)
    profile = stats_to_profile(stats, name=Path(filepath).stem)

    logger.info(
        f"交易分析: {stats.total_trades} 笔, "
        f"胜率 {stats.win_rate:.0%}, "
        f"平均持仓 {stats.avg_holding_days:.0f}天, "
        f"平均盈亏 {stats.avg_pnl_pct:+.2%}"
    )

    export_path = export_profile(profile, output_dir)
    logger.info(f"画像反推完成: {export_path}")
    return profile
