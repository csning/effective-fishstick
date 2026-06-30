"""择时 Agent — 多周期技术指标 + LLM 语境验证。

输入：股票代码列表（来自选股结果或持仓）
输出：buy / sell / hold 信号 + 置信度 + LLM 研判
"""

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from .base import BaseAgent, AgentContext, AgentResult
from .llm_client import get_llm
from data.market import get_daily_kline, get_index_daily


# ---------------------------------------------------------------------------
# 技术指标计算（纯函数，无副作用）
# ---------------------------------------------------------------------------

def _sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = _ema(series, fast)
    ema_slow = _ema(series, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)
    bar = 2 * (dif - dea)
    return dif, dea, bar


def _bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
    mid = _sma(series, period)
    std_dev = series.rolling(window=period, min_periods=period).std()
    upper = mid + std * std_dev
    lower = mid - std * std_dev
    return upper, mid, lower


def _volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    avg_vol = volume.rolling(window=period, min_periods=period).mean()
    return volume / avg_vol.replace(0, np.nan)


# ---------------------------------------------------------------------------
# 信号评分（权重打分，结果归一化到 [-1, 1]）
# ---------------------------------------------------------------------------

SIGNAL_THRESHOLDS = {
    "strong_buy": 0.6,
    "buy": 0.3,
    "sell": -0.3,
    "strong_sell": -0.6,
}

ACTION_CN = {
    "strong_buy": "🟢 强烈买入",
    "buy": "🟢 买入",
    "hold": "⚪ 持有/观望",
    "sell": "🔴 卖出",
    "strong_sell": "🔴 强烈卖出",
}


@dataclass
class TimingSignal:
    code: str
    name: str
    action: str
    confidence: float
    technical_score: float
    indicators: dict = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class TimingResult:
    signals: list
    market_context: str
    market_regime: str


def _compute_technical_score(df: pd.DataFrame) -> dict:
    """对单只股票的日线数据计算技术指标并输出综合评分。

    指标：MA5/10/20/60、MACD(12,26,9)、RSI(14)、Bollinger(20,2)、
          成交量比、ATR(14)、20日支撑/压力。
    """
    if len(df) < 60:
        return {"score": 0, "indicators": {}, "signals": []}

    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    latest_price = float(close.iloc[-1])

    ma5 = float(_sma(close, 5).iloc[-1])
    ma10 = float(_sma(close, 10).iloc[-1])
    ma20 = float(_sma(close, 20).iloc[-1])
    ma60 = float(_sma(close, 60).iloc[-1])

    dif_s, dea_s, bar_s = _macd(close)
    dif_last = float(dif_s.iloc[-1])
    dea_last = float(dea_s.iloc[-1])
    bar_last = float(bar_s.iloc[-1])
    dif_prev = float(dif_s.iloc[-2]) if len(dif_s) > 1 else dif_last
    dea_prev = float(dea_s.iloc[-2]) if len(dea_s) > 1 else dea_last

    rsi_last = float(_rsi(close, 14).iloc[-1])

    bb_upper_s, bb_mid_s, bb_lower_s = _bollinger(close)
    bb_upper_last = float(bb_upper_s.iloc[-1])
    bb_lower_last = float(bb_lower_s.iloc[-1])
    bb_width = (bb_upper_last - bb_lower_last) / float(bb_mid_s.iloc[-1]) * 100 if float(bb_mid_s.iloc[-1]) else 0

    vol_ratio_last = float(_volume_ratio(volume).iloc[-1])

    atr_last = float((high - low).rolling(14, min_periods=14).mean().iloc[-1])

    support = float(low.rolling(20, min_periods=20).min().iloc[-1])
    resistance = float(high.rolling(20, min_periods=20).max().iloc[-1])

    indicators = {
        "price": round(latest_price, 2),
        "ma5": round(ma5, 2), "ma10": round(ma10, 2),
        "ma20": round(ma20, 2), "ma60": round(ma60, 2),
        "macd_dif": round(dif_last, 4), "macd_dea": round(dea_last, 4),
        "macd_bar": round(bar_last, 4),
        "rsi": round(rsi_last, 1),
        "bb_upper": round(bb_upper_last, 2),
        "bb_lower": round(bb_lower_last, 2),
        "bb_width": round(bb_width, 1),
        "vol_ratio": round(vol_ratio_last, 2),
        "atr": round(atr_last, 2),
        "support": round(support, 2),
        "resistance": round(resistance, 2),
    }

    # 多因子评分（各子信号权重累加）
    score = 0.0
    signal_msgs: list[str] = []

    # 短期趋势：MA5 vs MA20
    if ma5 > ma20:
        score += 0.25
        signal_msgs.append("短期均线金叉")
    else:
        score -= 0.20
        signal_msgs.append("短期均线死叉")

    # 中期趋势：价格 vs MA60
    if latest_price > ma60:
        score += 0.15
    else:
        score -= 0.20
        signal_msgs.append("跌破60日均线")

    # MACD 方向 + 金叉/死叉
    if dif_last > dea_last and bar_last > 0:
        score += 0.15
        if dif_prev <= dea_prev:
            signal_msgs.append("MACD金叉")
    elif dif_last < dea_last:
        score -= 0.15
        if dif_prev >= dea_prev:
            signal_msgs.append("MACD死叉")

    # RSI 超买/超卖
    if not np.isnan(rsi_last):
        if rsi_last < 30:
            score += 0.15
            signal_msgs.append("RSI超卖")
        elif rsi_last > 70:
            score -= 0.15
            signal_msgs.append("RSI超买")

    # Bollinger
    if latest_price <= bb_lower_last:
        score += 0.10
        signal_msgs.append("触及布林下轨")
    elif latest_price >= bb_upper_last:
        score -= 0.10
        signal_msgs.append("触及布林上轨")

    # 成交量异动
    if not np.isnan(vol_ratio_last) and vol_ratio_last > 1.5:
        if score > 0:
            signal_msgs.append("放量上涨")
            score += 0.05
        else:
            signal_msgs.append("放量下跌")
            score -= 0.05

    score = max(-1.0, min(1.0, score))

    return {"score": score, "indicators": indicators, "signals": signal_msgs}


def _score_to_action(score: float) -> str:
    if score >= SIGNAL_THRESHOLDS["strong_buy"]:
        return "strong_buy"
    elif score >= SIGNAL_THRESHOLDS["buy"]:
        return "buy"
    elif score >= SIGNAL_THRESHOLDS["sell"]:
        return "hold"
    elif score >= SIGNAL_THRESHOLDS["strong_sell"]:
        return "sell"
    else:
        return "strong_sell"


# ---------------------------------------------------------------------------
# TimingAgent
# ---------------------------------------------------------------------------

class TimingAgent(BaseAgent):
    """技术面择时 Agent。

    对输入的股票代码列表逐只做技术面打分，然后交由 LLM
    做语境验证（结合市场状态过滤假信号）。

    用法：
        agent = TimingAgent(days=120, use_llm=True)
        result = await agent.run(ctx, symbols=["600519", "000858"])
    """

    name = "timing"

    def __init__(self, days: int = 120, use_llm: bool = True, top_signals: int = 10):
        self.days = days
        self.use_llm = use_llm
        self.top_signals = top_signals

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        symbols = kwargs.get("symbols", [])
        if not symbols:
            codes = ctx.flags.get("candidates", [])
            if codes:
                symbols = codes[:self.top_signals]
        if not symbols:
            return AgentResult(success=False, error="未提供股票代码", summary="timing: 无代码")

        logger.info(f"择时分析: {len(symbols)} 只股票")

        regime = await self._detect_regime()
        market_ctx = await self._get_market_context()

        signals: list[TimingSignal] = []
        for code in symbols:
            df = get_daily_kline(code, days=self.days)
            if df.empty:
                logger.warning(f"{code} K线数据为空，跳过")
                continue

            result = _compute_technical_score(df)
            tech_score = float(result["score"])
            confidence = abs(tech_score)
            action = _score_to_action(tech_score)
            name = str(df["name"].iloc[0]) if "name" in df.columns else code

            signals.append(TimingSignal(
                code=code, name=name, action=action,
                confidence=round(confidence, 3),
                technical_score=round(tech_score, 3),
                indicators=result["indicators"],
            ))

        signals.sort(key=lambda s: abs(s.technical_score), reverse=True)

        llm_analysis = ""
        if self.use_llm and signals:
            llm_analysis = await self._llm_context_check(
                signals[:10], market_ctx, regime
            )

        t_result = TimingResult(
            signals=signals,
            market_context=market_ctx,
            market_regime=regime,
        )

        buy_n = sum(1 for s in signals if s.action in ("strong_buy", "buy"))
        sell_n = sum(1 for s in signals if s.action in ("strong_sell", "sell"))

        return AgentResult(
            success=True, data=t_result, summary=self._format_result(t_result, llm_analysis),
            meta={
                "signal_count": len(signals),
                "buy_signals": buy_n,
                "sell_signals": sell_n,
                "regime": regime,
            },
        )

    async def _detect_regime(self) -> str:
        df = get_index_daily("sz399006", days=5)
        if df.empty:
            return "normal"
        cc = [c for c in df.columns if c.lower() == "close"]
        if not cc or len(df) < 3:
            return "normal"
        closes = df[cc[0]]
        chg_5d = (float(closes.iloc[-1]) - float(closes.iloc[0])) / float(closes.iloc[0]) * 100
        if chg_5d <= -5:
            return "bearish"
        elif chg_5d <= -2:
            return "defensive"
        elif chg_5d >= 2:
            return "bullish"
        return "normal"

    async def _get_market_context(self) -> str:
        parts = []
        for code, name in [("sh000001", "上证"), ("sz399001", "深证"), ("sz399006", "创业板")]:
            df = get_index_daily(code, days=2)
            if df.empty:
                continue
            cc = [c for c in df.columns if c.lower() == "close"]
            if not cc or len(df) < 2:
                continue
            latest = float(df[cc[0]].iloc[-1])
            prev = float(df[cc[0]].iloc[-2])
            chg = (latest - prev) / prev * 100
            parts.append(f"{name}: {latest:.0f} ({chg:+.2f}%)")
        return " | ".join(parts) if parts else "指数数据暂不可用"

    async def _llm_context_check(
        self, signals: list[TimingSignal], market_ctx: str, regime: str
    ) -> str:
        llm = get_llm()

        lines = []
        for i, s in enumerate(signals):
            ind = s.indicators
            lines.append(
                f"{i+1}. {s.code} {s.name} | {ACTION_CN.get(s.action,'?')} "
                f"得分={s.technical_score:.3f} | 价格={ind.get('price','?')} "
                f"RSI={ind.get('rsi','?')} MACD_DIF={ind.get('macd_dif','?')} "
                f"量比={ind.get('vol_ratio','?')}"
            )
        candidates_str = "\n".join(lines)

        regime_cn = {"bullish": "多头趋势", "bearish": "空头趋势",
                      "defensive": "偏防御", "normal": "震荡整理"}.get(regime, "正常")

        prompt = (
            f"以下是多因子技术指标择时结果。请结合当前市场环境做语境验证。\n\n"
            f"当前市场环境：{market_ctx}\n"
            f"市场状态判定：{regime_cn}\n\n"
            f"技术面信号 Top 10：\n\n{candidates_str}\n\n"
            f"请从以下角度分析（用中文输出，简洁具体）：\n"
            f"1. 整体信号质量评估\n"
            f"2. 最强买入信号 Top 3（简要理由+风险点）\n"
            f"3. 需要注意的卖出/风险信号\n"
            f"4. 当前市况下的操作策略建议"
        )

        try:
            analysis = await llm.reason(
                prompt,
                system="你是一位 A 股技术分析专家，擅长多因子指标综合研判。请用中文输出，具体可操作。",
            )
            return analysis
        except Exception as e:
            logger.error(f"LLM 择时语境验证失败: {e}")
            return f"[LLM 分析暂不可用: {e}]"

    def _format_result(self, result: TimingResult, llm_analysis: str) -> str:
        regime_cn = {"bullish": "多头趋势", "bearish": "空头趋势",
                      "defensive": "偏防御", "normal": "震荡整理"}.get(result.market_regime, "正常")
        buy_n = sum(1 for s in result.signals if s.action in ("strong_buy", "buy"))
        sell_n = sum(1 for s in result.signals if s.action in ("strong_sell", "sell"))

        lines = [
            "## 择时分析",
            "",
            f"**市场环境**：{result.market_context}",
            f"**市场状态**：{regime_cn}",
            f"**信号统计**：{buy_n} 买入 / {len(result.signals) - buy_n - sell_n} 持有 / {sell_n} 卖出",
            "",
            "| 代码 | 名称 | 信号 | 技术得分 | RSI | 量比 | 支撑 | 压力 |",
            "|------|------|------|----------|-----|------|------|------|",
        ]

        for s in result.signals[:15]:
            ind = s.indicators
            lines.append(
                f"| {s.code} | {s.name} | {ACTION_CN.get(s.action,'?')} | "
                f"{s.technical_score:.3f} | {ind.get('rsi','?')} | "
                f"{ind.get('vol_ratio','?')} | {ind.get('support','?')} | "
                f"{ind.get('resistance','?')} |"
            )

        if llm_analysis:
            lines.append("")
            lines.append("### AI 语境验证")
            lines.append("")
            lines.append(llm_analysis)

        return "\n".join(lines)
