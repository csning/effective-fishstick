"""选股 Agent -- 精选池 + 多因子打分 + 市场状态自适应。

数据来源：get_curated_stocks()（热门成交 Top 100 + 热门板块 Top 5 x 各 Top 20）
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import numpy as np
from loguru import logger

from .base import BaseAgent, AgentContext, AgentResult
from .llm_client import get_llm
from data.fundamental import get_curated_stocks
from data.market import get_index_daily


# ---------------------------------------------------------------------------
# 因子权重：正常 vs 防御（市场暴跌时自动切换）
# ---------------------------------------------------------------------------

FACTOR_WEIGHTS_NORMAL = {
    "net_inflow": 0.18,     # 主力净流入
    "pct_chg":   0.15,      # 当日涨幅
    "pe":        0.15,      # 市盈率（反向）
    "pb":        0.12,      # 市净率（反向）
    "turnover":  0.15,      # 换手率（适中最优）
    "market_cap":0.10,      # 市值（对数正向）
    "amplitude": 0.15,      # 振幅（反向）
}

FACTOR_WEIGHTS_DEFENSIVE = {
    "net_inflow": 0.10,
    "pct_chg":   0.05,
    "pe":        0.25,
    "pb":        0.20,
    "turnover":  0.10,
    "market_cap":0.20,
    "amplitude": 0.10,
}

FACTOR_WEIGHTS = FACTOR_WEIGHTS_NORMAL

BEAR_THRESHOLD = -2.0  # 创业板跌超 2% 切换防御

MIN_PRICE = 3.0
ST_PATTERN = "ST"
N_PATTERN = "N"
KCB_PREFIX = "688"


@dataclass
class StockScore:
    code: str
    name: str
    total_score: float
    factor_scores: dict
    raw: dict


@dataclass
class SelectionResult:
    candidates: list
    total_screened: int
    total_passed: int
    market_context: str
    market_regime: str


class StockSelector(BaseAgent):
    name = "stock_selector"

    def __init__(self, top_n: int = 20, use_llm: bool = True):
        self.top_n = top_n
        self.use_llm = use_llm

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        logger.info("拉取精选池...")
        df = get_curated_stocks()
        if df.empty:
            return AgentResult(success=False, error="精选池拉取失败")

        total = len(df)
        regime = await self._detect_regime()
        df = self._apply_filters(df)
        passed = len(df)
        logger.info("过滤后: {} -> {} 只 | 市场状态: {}", total, passed, regime)

        candidates = self._score_stocks(df, regime)
        market_ctx = await self._get_market_context()

        llm_analysis = ""
        if self.use_llm and candidates:
            llm_analysis = await self._llm_deep_analysis(candidates[:10], market_ctx, regime)

        result = SelectionResult(
            candidates=candidates[:self.top_n],
            total_screened=total,
            total_passed=passed,
            market_context=market_ctx,
            market_regime=regime,
        )

        summary = self._format_result(result, llm_analysis)
        return AgentResult(
            success=True, data=result, summary=summary,
            meta={"candidates": [s.code for s in candidates[:self.top_n]], "regime": regime},
        )

    async def _detect_regime(self) -> str:
        df = get_index_daily("sz399006", days=3)
        if df.empty:
            return "normal"
        close_col = [c for c in df.columns if c.lower() == "close"]
        if not close_col or len(df) < 2:
            return "normal"
        latest = df[close_col[0]].iloc[-1]
        prev = df[close_col[0]].iloc[-2]
        chg = (latest - prev) / prev * 100
        if chg <= BEAR_THRESHOLD:
            logger.info("创业板跌 {:.1f}%，切换防御权重", chg)
            return "defensive"
        return "normal"

    def _apply_filters(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if "name" in df.columns:
            df = df[~df["name"].str.contains(ST_PATTERN, na=False)]
            df = df[~df["name"].str.contains(N_PATTERN, na=False)]
        if "code" in df.columns:
            df = df[~df["code"].astype(str).str.startswith(KCB_PREFIX)]
        if "price" in df.columns:
            df = df[df["price"] >= MIN_PRICE]
        if "pe" in df.columns:
            df = df[df["pe"].notna() & (df["pe"] > 0)]
        if "pb" in df.columns:
            df = df[df["pb"].notna() & (df["pb"] > 0)]
        return df

    def _score_stocks(self, df: pd.DataFrame, regime: str) -> list:
        weights = FACTOR_WEIGHTS_DEFENSIVE if regime == "defensive" else FACTOR_WEIGHTS_NORMAL
        total_weight = sum(weights.values())
        scores = []
        factors = {}

        for factor in weights:
            col = factor
            if col not in df.columns:
                continue
            series = pd.to_numeric(df[col], errors="coerce").dropna()
            if len(series) < 5:
                continue
            mu, sigma = series.mean(), series.std()
            if sigma == 0:
                continue
            z = (series - mu) / sigma

            if factor in ("pe", "pb", "amplitude"):
                factors[factor] = -z.fillna(0)
            elif factor == "turnover":
                factors[factor] = -abs(z.fillna(0))
            elif factor == "market_cap":
                log_v = np.log1p(series)
                lz = (log_v - log_v.mean()) / log_v.std()
                factors[factor] = lz.fillna(0)
            else:
                factors[factor] = z.fillna(0)

        for idx, row in df.iterrows():
            total = 0.0
            breakdown = {}
            raw = {}
            for factor, weight in weights.items():
                if factor not in factors or factor not in row.index:
                    continue
                val = row[factor]
                if pd.isna(val):
                    continue
                z_score = factors[factor].get(idx, 0)
                total += z_score * weight
                breakdown[factor] = round(z_score, 3)
                try:
                    raw[factor] = round(float(val), 4)
                except (ValueError, TypeError):
                    raw[factor] = 0
            if total == 0:
                continue
            scores.append(StockScore(
                code=str(row.get("code", "")),
                name=str(row.get("name", "")),
                total_score=round(total / total_weight, 4),
                factor_scores=breakdown,
                raw=raw,
            ))

        scores.sort(key=lambda s: s.total_score, reverse=True)
        return scores

    async def _get_market_context(self) -> str:
        parts = []
        for code, name in [("sh000001", "上证"), ("sz399001", "深证"), ("sz399006", "创业板")]:
            df = get_index_daily(code, days=2)
            if df.empty:
                continue
            close_col = [c for c in df.columns if c.lower() == "close"]
            if not close_col or len(df) < 2:
                continue
            latest = df[close_col[0]].iloc[-1]
            prev = df[close_col[0]].iloc[-2]
            chg = (latest - prev) / prev * 100
            parts.append(f"{name}: {latest:.0f} ({chg:+.2f}%)")
        return " | ".join(parts) if parts else "指数数据暂不可用"

    async def _llm_deep_analysis(self, candidates, market_ctx: str, regime: str) -> str:
        llm = get_llm()

        lines = []
        for i, s in enumerate(candidates):
            lines.append(
                f"{i+1}. {s.code} {s.name} | 得分={s.total_score:.3f} | "
                f"PE={s.raw.get('pe',0):.1f} PB={s.raw.get('pb',0):.2f} "
                f"主力净流入={s.raw.get('net_inflow',0) or 0:.0f}万 "
                f"换手={s.raw.get('turnover',0):.1f}%"
            )
        candidates_str = "\n".join(lines)

        regime_cn = "防御模式（估值优先，动量降权）" if regime == "defensive" else "正常模式（平衡多因子）"
        prompt = (
            f"以下是今日精选池（热门成交+热门板块龙头）的多因子选股 Top 10。请用中文进行深度分析。\n\n"
            f"当前市场环境：{market_ctx}\n"
            f"当前模型模式：{regime_cn}\n\n"
            f"候选股票：\n\n{candidates_str}\n\n"
            f"请从以下角度分析（用中文输出，简洁具体）：\n"
            f"1. 整体评价\n2. 板块集中度\n3. Top 5 精选（简要理由）\n"
            f"4. 风险提示\n5. 操作建议"
        )

        try:
            analysis = await llm.reason(
                prompt,
                system="你是一位资深的 A 股量化策略师。请用中文输出，分析具体、可操作、不废话。",
            )
            return analysis
        except Exception as e:
            logger.error(f"LLM 深度分析失败: {e}")
            return f"[LLM 分析暂不可用: {e}]"

    def _format_result(self, result, llm_analysis: str) -> str:
        regime_cn = "防御" if result.market_regime == "defensive" else "正常"
        lines = [
            "## 选股结果",
            "",
            f"**筛选范围**：精选池 {result.total_screened} 只 -> {result.total_passed} 只通过过滤",
            f"**市场环境**：{result.market_context}",
            f"**因子模式**：{regime_cn}",
            "",
            f"### Top {min(self.top_n, len(result.candidates))} 候选",
            "",
            "| 排名 | 代码 | 名称 | 得分 | PE | PB | 主力净流入(万) | 换手率 |",
            "|------|------|------|------|----|----|----------------|--------|",
        ]
        for i, s in enumerate(result.candidates[:self.top_n]):
            inflow = s.raw.get("net_inflow", 0) or 0
            lines.append(
                f"| {i+1} | {s.code} | {s.name} | {s.total_score:.3f} | "
                f"{s.raw.get('pe',0):.1f} | {s.raw.get('pb',0):.2f} | "
                f"{inflow:.0f} | {s.raw.get('turnover',0):.1f}% |"
            )

        if llm_analysis:
            lines.append("")
            lines.append("### AI 深度研判")
            lines.append("")
            lines.append(llm_analysis)

        return "\n".join(lines)
