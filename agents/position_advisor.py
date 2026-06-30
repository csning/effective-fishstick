"""持仓建议 Agent — 持仓诊断、行业集中度、再平衡建议。

输入：持仓列表（从 AgentContext.holdings 或显式传入）
输出：逐票诊断 + 行业分布 + 再平衡方案
"""

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from loguru import logger

from .base import BaseAgent, AgentContext, AgentResult
from .llm_client import get_llm
from data.market import get_daily_kline, get_index_daily
from data.fundamental import get_active_top_n


SECTOR_PREFIX = {
    "600": "主板", "601": "主板", "603": "主板", "605": "主板",
    "000": "深主板", "001": "深主板", "002": "中小板", "003": "中小板",
    "300": "创业板", "301": "创业板",
    "688": "科创板",
}


def _infer_sector(code: str) -> str:
    for prefix, sector in SECTOR_PREFIX.items():
        if code.startswith(prefix):
            return sector
    return "其他"


@dataclass
class PositionDiagnosis:
    code: str
    name: str
    shares: int
    cost: float
    current_price: float
    market_value: float
    pnl: float
    pnl_pct: float
    weight: float
    sector: str
    signal: str = "hold"
    issues: list = field(default_factory=list)
    highlights: list = field(default_factory=list)


@dataclass
class PortfolioAnalysis:
    positions: list[PositionDiagnosis]
    total_value: float
    total_pnl: float
    total_pnl_pct: float
    sector_weights: dict
    concentration_warnings: list[str]


class PositionAdvisor(BaseAgent):
    """持仓诊断与再平衡建议 Agent。"""

    name = "position_advisor"

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        positions_raw = kwargs.get("positions") or ctx.holdings.get("positions", [])
        if not positions_raw:
            return AgentResult(
                success=False, error="未提供持仓数据",
                summary="position_advisor: 无持仓"
            )

        logger.info(f"持仓诊断: {len(positions_raw)} 只")

        price_map = await self._fetch_prices(
            [p["code"] for p in positions_raw]
        )

        diagnoses: list[PositionDiagnosis] = []
        for pos in positions_raw:
            code = str(pos.get("code", ""))
            name = str(pos.get("name", code))
            shares = int(pos.get("shares", 0))
            cost = float(pos.get("cost", 0))

            current_price = price_map.get(code)
            if current_price is None or current_price <= 0:
                logger.warning(f"{code} 无实时价格，跳过")
                continue

            mv = shares * current_price
            pnl = mv - shares * cost
            pnl_pct = (current_price - cost) / cost if cost > 0 else 0
            sector = _infer_sector(code)

            issues, highlights, signal = self._quick_check(code, current_price)

            diagnoses.append(PositionDiagnosis(
                code=code, name=name, shares=shares, cost=cost,
                current_price=round(current_price, 2),
                market_value=round(mv, 2),
                pnl=round(pnl, 2),
                pnl_pct=round(pnl_pct, 4),
                weight=0,
                sector=sector,
                signal=signal,
                issues=issues,
                highlights=highlights,
            ))

        if not diagnoses:
            return AgentResult(success=False, error="所有持仓均无实时价格")

        total_value = sum(d.market_value for d in diagnoses)
        total_pnl = sum(d.pnl for d in diagnoses)
        total_pnl_pct = total_pnl / (total_value - total_pnl) if total_value != total_pnl else 0

        for d in diagnoses:
            d.weight = d.market_value / total_value if total_value > 0 else 0

        sector_weights: dict[str, float] = {}
        for d in diagnoses:
            sector_weights[d.sector] = sector_weights.get(d.sector, 0) + d.weight

        concentration_warnings: list[str] = []
        for sector, w in sector_weights.items():
            if w > 0.40:
                concentration_warnings.append(f"{sector} 占比 {w:.0%}，超过 40% 警戒线")
            elif w > 0.30:
                concentration_warnings.append(f"{sector} 占比 {w:.0%}，偏高，建议关注")

        portfolio = PortfolioAnalysis(
            positions=diagnoses,
            total_value=round(total_value, 2),
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 4),
            sector_weights={k: round(v, 3) for k, v in sector_weights.items()},
            concentration_warnings=concentration_warnings,
        )

        llm_analysis = ""
        if self.use_llm:
            llm_analysis = await self._llm_diagnosis(portfolio, ctx.risk_level)

        summary = self._format_result(portfolio, llm_analysis, ctx.risk_level)
        return AgentResult(
            success=True, data=portfolio, summary=summary,
            meta={
                "position_count": len(diagnoses),
                "total_value": portfolio.total_value,
                "total_pnl": portfolio.total_pnl,
                "sector_dist": list(sector_weights.keys()),
            },
        )

    async def _fetch_prices(self, codes: list[str]) -> dict[str, float]:
        price_map: dict[str, float] = {}
        try:
            df = get_active_top_n(n=500, sort_by="amount")
            if df.empty:
                return price_map

            for _, row in df.iterrows():
                code = str(row.get("code", ""))
                price = row.get("price")
                if code in codes and price is not None:
                    price_map[code] = float(price) if float(price) > 0 else 0
        except Exception as e:
            logger.warning(f"批量获取价格失败: {e}")

        for code in codes:
            if code not in price_map:
                try:
                    df = get_daily_kline(code, days=5)
                    if not df.empty:
                        cc = [c for c in df.columns if c.lower() == "close"]
                        if cc:
                            price_map[code] = float(df[cc[0]].iloc[-1])
                except Exception:
                    pass

        return price_map

    def _quick_check(self, code: str, current_price: float) -> tuple[list, list, str]:
        issues: list[str] = []
        highlights: list[str] = []
        signal = "hold"

        try:
            from .timing import _compute_technical_score, _score_to_action
            df = get_daily_kline(code, days=120)
            if not df.empty:
                result = _compute_technical_score(df)
                score = float(result["score"])
                signal = _score_to_action(score)
                sig_msgs = result.get("signals", [])

                for msg in sig_msgs:
                    if "死叉" in msg or "超买" in msg or "跌破" in msg or "放量下跌" in msg:
                        issues.append(msg)
                    else:
                        highlights.append(msg)
        except Exception:
            pass

        return issues, highlights, signal

    async def _llm_diagnosis(
        self, portfolio: PortfolioAnalysis, risk_level: int
    ) -> str:
        llm = get_llm()

        lines = ["**当前持仓明细**\n"]
        for i, d in enumerate(portfolio.positions):
            emoji = "🟢" if d.pnl_pct >= 0 else "🔴"
            lines.append(
                f"{i+1}. {d.code} {d.name} | "
                f"成本 {d.cost:.2f} -> 现价 {d.current_price:.2f} | "
                f"{emoji} {d.pnl_pct:+.2%} | 仓位 {d.weight:.1%} | "
                f"板块 {d.sector} | 信号 {d.signal}"
            )

        sector_str = ", ".join(
            f"{s}:{w:.0%}" for s, w in portfolio.sector_weights.items()
        )
        warnings_str = "\n".join(f"  {w}" for w in portfolio.concentration_warnings) or "无"

        prompt = (
            f"以下是当前投资组合持仓诊断结果。请给出综合评估和再平衡建议。\n\n"
            f"风险等级：{risk_level}/5\n"
            f"总市值：{portfolio.total_value:,.0f}\n"
            f"总盈亏：{portfolio.total_pnl:+,.0f}（{portfolio.total_pnl_pct:+.2%}）\n"
            f"行业分布：{sector_str}\n"
            f"集中度预警：\n{warnings_str}\n\n"
            f"持仓明细：\n\n" + "\n".join(lines) + "\n\n"
            f"请从以下角度分析（用中文输出，简洁具体）：\n"
            f"1. 组合整体健康度评估\n"
            f"2. 需要减仓/清仓的标的（说明理由）\n"
            f"3. 行业集中度风险与优化方向\n"
            f"4. 在当前风险等级下，建议的目标仓位分配\n"
            f"5. 下一步操作清单（优先排序）"
        )

        try:
            analysis = await llm.reason(
                prompt,
                system="你是一位资深的投资组合管理顾问，擅长 A 股仓位管理和再平衡策略。请用中文输出，建议具体可执行。",
            )
            return analysis
        except Exception as e:
            logger.error(f"LLM 持仓诊断失败: {e}")
            return f"[LLM 分析暂不可用: {e}]"

    def _format_result(
        self, portfolio: PortfolioAnalysis, llm_analysis: str, risk_level: int
    ) -> str:
        pnl_emoji = "🟢" if portfolio.total_pnl >= 0 else "🔴"
        lines = [
            "## 持仓诊断",
            "",
            f"**风险等级**：{risk_level}/5",
            f"**总市值**：{portfolio.total_value:,.0f}",
            f"**总盈亏**：{pnl_emoji} {portfolio.total_pnl:+,.0f}（{portfolio.total_pnl_pct:+.2%}）",
            "",
            f"### 行业分布",
        ]

        for sector, w in sorted(portfolio.sector_weights.items(), key=lambda x: -x[1]):
            bar = "|" * max(1, int(w * 20))
            lines.append(f"- {sector}: {bar} {w:.0%}")

        if portfolio.concentration_warnings:
            lines.append("")
            lines.append("**集中度预警**")
            for w in portfolio.concentration_warnings:
                lines.append(f"- {w}")

        lines.append("")
        lines.append("### 逐票诊断")
        lines.append("")
        lines.append(
            "| 代码 | 名称 | 成本 | 现价 | 盈亏 | 仓位 | 板块 | 信号 |"
        )
        lines.append(
            "|------|------|------|------|------|------|------|------|"
        )

        for d in portfolio.positions:
            lines.append(
                f"| {d.code} | {d.name} | {d.cost:.2f} | {d.current_price:.2f} | "
                f"{d.pnl_pct:+.2%} | {d.weight:.1%} | {d.sector} | {d.signal} |"
            )

        if llm_analysis:
            lines.append("")
            lines.append("### AI 综合诊断")
            lines.append("")
            lines.append(llm_analysis)

        return "\n".join(lines)
