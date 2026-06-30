"""风控引擎 — 自适应风险评估。

综合四个维度实时打分，映射为 1-5 级风控等级，
每级控制仓位上限和止损宽度。
"""

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from data.market import get_index_daily, get_market_breadth, get_us_market_snapshot


@dataclass
class RiskAssessment:
    level: int
    score: float
    sub_scores: dict
    position_cap: float
    stop_loss_pct: float
    reasoning: str


class RiskEngine:
    """自适应风控引擎。

    四个维度 + 权重（默认平衡型，可通过策略画像覆写）：
    - trend (0.35): 趋势强度 — 创业板 MA 方向 + 市场宽度
    - volatility (0.25): 波动率 — ATR/标准差，越高越危险
    - macro (0.20): 宏观 — 美股联动方向
    - sentiment (0.20): 情绪 — 涨跌比 + 成交量
    """

    WEIGHTS = {
        "trend": 0.35,
        "volatility": 0.25,
        "macro": 0.20,
        "sentiment": 0.20,
    }

    LEVEL_THRESHOLDS = [
        (0.0, 1), (0.3, 2), (0.5, 3), (0.7, 4), (0.85, 5),
    ]

    BENCHMARK_INDEX = "sz399006"

    def __init__(
        self,
        position_caps: Optional[dict] = None,
        stop_loss_pcts: Optional[dict] = None,
        weights: Optional[dict] = None,
        auto_adjust: bool = True,
    ):
        self.position_caps = position_caps or {
            1: 0.30, 2: 0.50, 3: 0.70, 4: 0.90, 5: 1.00,
        }
        self.stop_loss_pcts = stop_loss_pcts or {
            1: 0.03, 2: 0.05, 3: 0.08, 4: 0.12, 5: 0.15,
        }
        self.weights = weights or dict(self.WEIGHTS)
        self.auto_adjust = auto_adjust
        self._locked_level: Optional[int] = None

    def lock_level(self, level: int) -> None:
        if not 1 <= level <= 5:
            raise ValueError(f"风险等级必须在 1-5 之间，收到 {level}")
        self._locked_level = level
        self.auto_adjust = False
        logger.info(f"风控等级已手动锁定: {level}/5")

    def unlock(self) -> None:
        self._locked_level = None
        self.auto_adjust = True
        logger.info("风控等级恢复自动评估")

    def assess(self, ctx: Optional[dict] = None) -> RiskAssessment:
        ctx = ctx or {}

        # 手动锁定优先
        manual_level = ctx.get("risk_level")
        if not self.auto_adjust or self._locked_level is not None:
            level = self._locked_level or int(manual_level or 3)
            return RiskAssessment(
                level=level,
                score=float(level) / 5,
                sub_scores={},
                position_cap=self.position_caps.get(level, 0.7),
                stop_loss_pct=self.stop_loss_pcts.get(level, 0.08),
                reasoning=f"手动锁定等级 {level}/5",
            )

        sub_scores = self._compute_sub_scores()
        score = self._composite_score(sub_scores)
        level = self._score_to_level(score)
        reasoning = self._build_reasoning(sub_scores, score, level)

        return RiskAssessment(
            level=level,
            score=round(score, 3),
            sub_scores=sub_scores,
            position_cap=self.position_caps.get(level, 0.7),
            stop_loss_pct=self.stop_loss_pcts.get(level, 0.08),
            reasoning=reasoning,
        )

    def _compute_sub_scores(self) -> dict:
        return {
            "trend": self._trend_score(),
            "volatility": self._volatility_score(),
            "macro": self._macro_score(),
            "sentiment": self._sentiment_score(),
        }

    def _composite_score(self, sub: dict) -> float:
        total = 0.0
        for dim, weight in self.weights.items():
            total += sub.get(dim, 0.5) * weight
        return max(0.0, min(1.0, total))

    def _score_to_level(self, score: float) -> int:
        for threshold, level in reversed(self.LEVEL_THRESHOLDS):
            if score >= threshold:
                return level
        return 1

    # ── 趋势评分 ──

    def _trend_score(self) -> float:
        df = get_index_daily(self.BENCHMARK_INDEX, days=120)
        if df.empty or len(df) < 20:
            return 0.5

        cc = [c for c in df.columns if c.lower() == "close"]
        if not cc:
            return 0.5

        close = df[cc[0]].astype(float).tail(60)
        if len(close) < 20:
            return 0.5

        ma20 = float(close.rolling(20, min_periods=20).mean().iloc[-1])
        ma60 = float(close.rolling(60, min_periods=60).mean().iloc[-1]) if len(close) >= 60 else ma20
        price = float(close.iloc[-1])

        if price > ma20 > ma60:
            ma_score = 0.9
        elif price > ma20:
            ma_score = 0.65
        elif price > ma60:
            ma_score = 0.5
        elif price < ma20 < ma60:
            ma_score = 0.1
        elif price < ma20:
            ma_score = 0.35
        else:
            ma_score = 0.5

        breadth = get_market_breadth()
        up_ratio = breadth.get("up_ratio", 0.5)
        if up_ratio > 0.6:
            bonus = 0.05
        elif up_ratio < 0.4:
            bonus = -0.05
        else:
            bonus = 0.0

        return max(0.0, min(1.0, ma_score + bonus))

    # ── 波动率评分 ──

    def _volatility_score(self) -> float:
        df = get_index_daily(self.BENCHMARK_INDEX, days=60)
        if df.empty or len(df) < 20:
            return 0.5

        cc = [c for c in df.columns if c.lower() == "close"]
        hh = [c for c in df.columns if c.lower() == "high"]
        ll = [c for c in df.columns if c.lower() == "low"]
        if not cc:
            return 0.5

        close = df[cc[0]].astype(float).tail(20)
        if len(close) < 5:
            return 0.5

        returns = close.pct_change().dropna()
        std_daily = float(returns.std())

        if hh and ll:
            high = df[hh[0]].astype(float).tail(14)
            low = df[ll[0]].astype(float).tail(14)
            tr = pd.concat([
                high - low,
                abs(high - close.shift(1)),
                abs(low - close.shift(1)),
            ], axis=1).max(axis=1)
            atr = float(tr.mean())
        else:
            atr = float(close.diff().abs().mean())

        atr_pct = atr / float(close.iloc[-1]) * 100 if float(close.iloc[-1]) > 0 else 2.0

        if std_daily < 0.008:
            vol_score = 0.85
        elif std_daily < 0.015:
            vol_score = 0.65
        elif std_daily < 0.025:
            vol_score = 0.45
        elif std_daily < 0.035:
            vol_score = 0.25
        else:
            vol_score = 0.1

        if atr_pct > 4:
            vol_score -= 0.15
        elif atr_pct > 3:
            vol_score -= 0.08

        return max(0.0, min(1.0, vol_score))

    # ── 宏观评分 ──

    def _macro_score(self) -> float:
        df_cn = get_index_daily("sh000001", days=5)
        cn_dir = 0.0
        if not df_cn.empty and len(df_cn) >= 3:
            cc = [c for c in df_cn.columns if c.lower() == "close"]
            if cc:
                closes = df_cn[cc[0]].astype(float)
                cn_dir = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0])

        us_dir = 0.0
        try:
            us = get_us_market_snapshot()
            parts = []
            for df in us.values():
                if df.empty or len(df) < 2:
                    continue
                cc = [c for c in df.columns if c.lower() == "close"]
                if cc:
                    chg = (float(df[cc[0]].iloc[-1]) - float(df[cc[0]].iloc[-2])) / float(df[cc[0]].iloc[-2])
                    parts.append(chg)
            if parts:
                us_dir = sum(parts) / len(parts)
        except Exception:
            pass

        if cn_dir > 0.01 and us_dir > -0.005:
            return 0.8
        elif cn_dir > 0:
            return 0.6
        elif cn_dir > -0.01:
            return 0.5
        elif us_dir < -0.01:
            return 0.2
        return 0.4

    # ── 情绪评分 ──

    def _sentiment_score(self) -> float:
        breadth = get_market_breadth()
        if not breadth:
            return 0.5

        up_ratio = breadth.get("up_ratio", 0.5)
        up_count = breadth.get("up", 0)
        down_count = breadth.get("down", 0)
        avg_pct = breadth.get("avg_pct", 0)

        if up_ratio > 0.70:
            b_score = 0.85
        elif up_ratio > 0.55:
            b_score = 0.65
        elif up_ratio > 0.45:
            b_score = 0.50
        elif up_ratio > 0.30:
            b_score = 0.35
        else:
            b_score = 0.15

        total = up_count + down_count
        if total > 0:
            if up_count > total * 0.8:
                b_score = 0.9
            elif down_count > total * 0.8:
                b_score = 0.1

        if avg_pct > 2:
            b_score = min(1.0, b_score + 0.1)
        elif avg_pct < -2:
            b_score = max(0.0, b_score - 0.1)

        return max(0.0, min(1.0, b_score))

    # ── 推理说明 ──

    def _build_reasoning(self, sub: dict, score: float, level: int) -> str:
        parts = []
        dim_cn = {"trend": "趋势", "volatility": "波动率", "macro": "宏观", "sentiment": "情绪"}

        for dim, s in sub.items():
            if s >= 0.7:
                parts.append(f"{dim_cn.get(dim, dim)}良好({s:.2f})")
            elif s <= 0.3:
                parts.append(f"{dim_cn.get(dim, dim)}危险({s:.2f})")

        details = " | ".join(parts) if parts else "各项指标中性"

        cap = self.position_caps.get(level, 0.7)
        stop = self.stop_loss_pcts.get(level, 0.08)

        return (
            f"综合评分 {score:.2f} -> 等级 {level}/5 | {details}\n"
            f"仓位上限 {cap:.0%} | 止损宽度 {stop:.1%}"
        )
