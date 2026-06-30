"""简化回测引擎 — 历史数据模拟 + 选股策略验证 + 基准对比。

策略：每日等权 Top-N 活跃股，按日调仓。
基准：沪深300指数。
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from data.market import get_daily_kline, get_index_daily

RISK_FREE_RATE = 0.03
TRADING_DAYS_PER_YEAR = 252


@dataclass
class BacktestResult:
    """回测结果。"""
    start_date: str
    end_date: str
    total_return: float
    annualized_return: float
    sharpe_ratio: float
    max_drawdown: float
    win_rate: float
    benchmark_return: float
    benchmark_annualized: float
    daily_returns: list = field(default_factory=list)
    benchmark_returns: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


class BacktestEngine:
    """简化回测引擎。

    用法:
        engine = BacktestEngine(
            start="2025-01-01", end="2025-06-30",
            symbols=["600519", "000858", "300750"],
            benchmark="sh000300",
        )
        result = engine.run()
    """

    def __init__(
        self,
        start: str,
        end: str,
        symbols: list[str],
        benchmark: str = "sh000300",
        top_n: int = 10,
    ):
        self.start = start
        self.end = end
        self.symbols = symbols
        self.benchmark_code = benchmark
        self.top_n = min(top_n, len(symbols)) if symbols else top_n

    def run(self) -> BacktestResult:
        """执行回测。"""
        logger.info(f"回测: {self.start} -> {self.end}, {len(self.symbols)} 只")

        # 拉取基准数据
        bench_df = get_index_daily(self.benchmark_code, days=365 * 2)
        bench_returns = self._daily_returns(bench_df)

        # 拉取所有股票数据
        stock_data: dict[str, pd.DataFrame] = {}
        for code in self.symbols:
            df = get_daily_kline(code, days=365 * 2)
            if not df.empty and len(df) >= 10:
                stock_data[code] = df

        if not stock_data:
            return BacktestResult(
                start_date=self.start, end_date=self.end,
                total_return=0, annualized_return=0,
                sharpe_ratio=0, max_drawdown=0, win_rate=0,
                benchmark_return=0, benchmark_annualized=0,
            )

        portfolio_returns = self._simulate_equal_weight(stock_data)
        if not portfolio_returns:
            return BacktestResult(
                start_date=self.start, end_date=self.end,
                total_return=0, annualized_return=0,
                sharpe_ratio=0, max_drawdown=0, win_rate=0,
                benchmark_return=0, benchmark_annualized=0,
            )

        daily_returns = portfolio_returns[1:]  # skip first NaN
        bench_daily = bench_returns[1:] if len(bench_returns) > 1 else []

        metrics = self._compute_metrics(daily_returns, bench_daily)
        return BacktestResult(
            start_date=self.start,
            end_date=self.end,
            **metrics,
            daily_returns=[round(r, 6) for r in daily_returns],
            benchmark_returns=[round(r, 6) for r in bench_daily],
            equity_curve=self._cumulative_returns(daily_returns),
        )

    def _daily_returns(self, df: pd.DataFrame) -> list[float]:
        if df.empty:
            return []

        cc = [c for c in df.columns if c.lower() == "close"]
        if not cc:
            return []

        close = df[cc[0]].astype(float)
        if "date" in df.columns:
            close.index = pd.to_datetime(df["date"])
        elif df.index.name and df.index.name.lower() == "date":
            pass
        else:
            close = close.reset_index(drop=True)

        returns = close.pct_change().dropna()
        return [float(r) for r in returns.values]

    def _simulate_equal_weight(
        self, stock_data: dict[str, pd.DataFrame]
    ) -> list[float]:
        """模拟每日等权 Top-N 组合收益。

        每天取前 N 只股票（按当日前收盘价排序），等权配置。
        """
        if not stock_data:
            return []

        # 收集所有交易日
        all_dates = set()
        for df in stock_data.values():
            if "date" in df.columns:
                all_dates.update(
                    d.strftime("%Y-%m-%d")
                    for d in pd.to_datetime(df["date"]).dropna()
                )

        sorted_dates = sorted(all_dates)
        if len(sorted_dates) < 2:
            return []

        # 过滤回测区间
        start_dt = datetime.strptime(self.start, "%Y-%m-%d")
        end_dt = datetime.strptime(self.end, "%Y-%m-%d")
        valid_dates = [
            d for d in sorted_dates
            if start_dt <= datetime.strptime(d, "%Y-%m-%d") <= end_dt
        ]

        if len(valid_dates) < 2:
            return []

        daily_returns = []
        for i, date_str in enumerate(valid_dates[1:], 1):
            day_returns = []
            for code, df in stock_data.items():
                if "date" not in df.columns:
                    continue
                df_dates = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
                prev_mask = df_dates == valid_dates[i - 1]
                curr_mask = df_dates == date_str

                if prev_mask.any() and curr_mask.any():
                    cc = [c for c in df.columns if c.lower() == "close"]
                    if cc:
                        prev_price = float(df.loc[prev_mask, cc[0]].iloc[0])
                        curr_price = float(df.loc[curr_mask, cc[0]].iloc[0])
                        if prev_price > 0:
                            day_returns.append(
                                (code, (curr_price - prev_price) / prev_price)
                            )

            if day_returns:
                # 等权
                avg_return = sum(r for _, r in day_returns) / len(day_returns)
                daily_returns.append(avg_return)
            else:
                daily_returns.append(0.0)

        return daily_returns

    def _compute_metrics(
        self, returns: list[float], bench: list[float]
    ) -> dict:
        if not returns:
            return {}

        total_ret = float(np.prod([1 + r for r in returns]) - 1)
        n_days = len(returns)
        ann_ret = (1 + total_ret) ** (TRADING_DAYS_PER_YEAR / n_days) - 1 if n_days > 0 else 0

        std_daily = float(np.std(returns)) if len(returns) > 1 else 0
        sharpe = (
            (ann_ret - RISK_FREE_RATE) / (std_daily * np.sqrt(TRADING_DAYS_PER_YEAR))
            if std_daily > 0
            else 0
        )

        cumulative = self._cumulative_returns(returns)
        peak = np.maximum.accumulate(cumulative)
        drawdowns = (np.array(cumulative) - peak) / peak
        max_dd = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0

        win_rate = sum(1 for r in returns if r > 0) / len(returns) if returns else 0

        # 基准
        bench_total = float(np.prod([1 + r for r in bench]) - 1) if bench else 0
        bench_ann = (
            (1 + bench_total) ** (TRADING_DAYS_PER_YEAR / len(bench)) - 1
            if bench and len(bench) > 0
            else 0
        )

        return {
            "total_return": round(total_ret, 4),
            "annualized_return": round(ann_ret, 4),
            "sharpe_ratio": round(sharpe, 2),
            "max_drawdown": round(max_dd, 4),
            "win_rate": round(win_rate, 4),
            "benchmark_return": round(bench_total, 4),
            "benchmark_annualized": round(bench_ann, 4),
        }

    def _cumulative_returns(self, returns: list[float]) -> list[float]:
        curve = [1.0]
        for r in returns:
            curve.append(curve[-1] * (1 + r))
        return [round(v, 4) for v in curve]

    def format_result(self, result: BacktestResult) -> str:
        lines = [
            f"## 回测结果: {result.start_date} ~ {result.end_date}",
            "",
            f"| 指标 | 策略 | 基准(沪深300) |",
            f"|------|------|---------------|",
            f"| 总收益率 | {result.total_return:+.2%} | {result.benchmark_return:+.2%} |",
            f"| 年化收益率 | {result.annualized_return:+.2%} | {result.benchmark_annualized:+.2%} |",
            f"| Sharpe 比率 | {result.sharpe_ratio:.2f} | — |",
            f"| 最大回撤 | {result.max_drawdown:.2%} | — |",
            f"| 胜率 | {result.win_rate:.1%} | — |",
        ]
        return "\n".join(lines)
