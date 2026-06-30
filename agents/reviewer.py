"""每日复盘 Agent — 盘后结构化复盘报告。

数据源：A 股三大指数、行业资金流、市场宽度、新闻、美股指数。
LLM 综合生成：市场综述、板块轮动、组合表现、明日展望。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from .base import BaseAgent, AgentContext, AgentResult
from .llm_client import get_llm
from data.market import (
    get_index_daily, get_sector_flow, get_market_breadth,
    get_us_market_snapshot,
)
from data.news import get_market_news


TZ = timezone(timedelta(hours=8))


@dataclass
class ReviewReport:
    date: str
    market_summary: str
    index_details: list
    sector_flow_top: list
    market_breadth: dict
    portfolio_pnl: float
    highlights: list
    risks: list
    llm_analysis: str


class DailyReviewer(BaseAgent):
    """盘后结构化复盘报告 Agent。

    数据采集：
    - A 股三大指数（上证/深证/创业板）日涨跌
    - 行业资金流 Top 5 流入 / Top 5 流出
    - 市场宽度（涨跌家数比）
    - 市场新闻摘编
    - 美股收盘（如有）

    LLM 综合生成：
    - 整体评价 + 关键事件摘要
    - 资金流向解读
    - 组合表现评估（可选）
    - 明日操作策略建议
    """

    name = "reviewer"

    def __init__(self, use_llm: bool = True):
        self.use_llm = use_llm

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        logger.info(f"每日复盘 — {today}")

        index_details = self._collect_index_data()
        sector_data = self._collect_sector_flow()
        breadth = self._collect_market_breadth()
        news_text = self._collect_news()
        us_snapshot = self._collect_us_market()

        portfolio_pnl = float(ctx.holdings.get("today_pnl", 0))
        market_summary = self._build_market_summary(index_details, breadth)

        highlights, risks = self._extract_highlights_risks(
            index_details, sector_data, breadth
        )

        llm_analysis = ""
        if self.use_llm:
            llm_analysis = await self._llm_review(
                today, market_summary, index_details,
                sector_data, breadth, news_text, us_snapshot,
                portfolio_pnl, ctx.risk_level,
            )

        report = ReviewReport(
            date=today,
            market_summary=market_summary,
            index_details=index_details,
            sector_flow_top=sector_data,
            market_breadth=breadth,
            portfolio_pnl=portfolio_pnl,
            highlights=highlights,
            risks=risks,
            llm_analysis=llm_analysis,
        )

        summary = self._format_result(report, ctx.risk_level)
        return AgentResult(
            success=True, data=report, summary=summary,
            meta={
                "date": today,
                "up_ratio": breadth.get("up_ratio", 0),
                "portfolio_pnl": portfolio_pnl,
            },
        )

    def _collect_index_data(self) -> list:
        indices = []
        for code, name in [
            ("sh000001", "上证指数"), ("sz399001", "深证成指"),
            ("sz399006", "创业板指"), ("sh000688", "科创50"),
        ]:
            df = get_index_daily(code, days=2)
            if df.empty or len(df) < 2:
                continue
            cc = [c for c in df.columns if c.lower() == "close"]
            vol = [c for c in df.columns if c.lower() == "volume"]
            if not cc:
                continue
            latest = float(df[cc[0]].iloc[-1])
            prev = float(df[cc[0]].iloc[-2])
            chg = (latest - prev) / prev * 100
            vol_last = float(df[vol[0]].iloc[-1]) if vol else 0
            indices.append({
                "name": name, "code": code,
                "close": round(latest, 2),
                "chg_pct": round(chg, 2),
                "volume": int(vol_last),
            })
        return indices

    def _collect_sector_flow(self) -> list:
        df = get_sector_flow()
        if df.empty:
            return []

        items = []
        name_col = None
        flow_col = None
        for c in df.columns:
            cl = c.lower()
            if "名称" in c or cl == "name":
                name_col = c
            if "主力净流入-净额" in c:
                flow_col = c

        if name_col and flow_col:
            df_sorted = df.sort_values(by=flow_col, ascending=False)
            for _, row in df_sorted.head(8).iterrows():
                val = row[flow_col]
                items.append({
                    "name": str(row[name_col]),
                    "flow": round(float(val) / 1e8, 2) if val else 0,
                })
        return items

    def _collect_market_breadth(self) -> dict:
        return get_market_breadth()

    def _collect_news(self) -> str:
        try:
            df = get_market_news(days=1)
            if df.empty:
                return ""
            title_col = [c for c in df.columns if "标题" in c or c.lower() == "title"]
            if title_col:
                titles = df[title_col[0]].dropna().head(15).tolist()
                return "\n".join(f"- {t}" for t in titles)
        except Exception as e:
            logger.warning(f"新闻采集失败: {e}")
        return ""

    def _collect_us_market(self) -> str:
        try:
            snapshot = get_us_market_snapshot()
            if not snapshot:
                return ""
            parts = []
            for code, df in snapshot.items():
                if df.empty:
                    continue
                cc = [c for c in df.columns if c.lower() == "close"]
                if not cc:
                    continue
                name = {".DJI": "道指", ".IXIC": "纳指", ".SPX": "标普"}.get(code, code)
                chg = (float(df[cc[0]].iloc[-1]) - float(df[cc[0]].iloc[-2])) / float(df[cc[0]].iloc[-2]) * 100
                parts.append(f"{name}: {chg:+.2f}%")
            return " | ".join(parts)
        except Exception:
            return ""

    def _build_market_summary(
        self, indices: list, breadth: dict
    ) -> str:
        parts = []
        for ind in indices:
            parts.append(f"{ind['name']}: {ind['close']:.0f} ({ind['chg_pct']:+.2f}%)")
        if breadth:
            up_ratio = breadth.get("up_ratio", 0)
            if up_ratio > 0.6:
                mood = "普涨"
            elif up_ratio > 0.4:
                mood = "分化"
            else:
                mood = "普跌"
            parts.append(
                f"涨跌比 {breadth.get('up',0)}:{breadth.get('down',0)}（{mood}）"
            )
        return " | ".join(parts)

    def _extract_highlights_risks(
        self, indices, sector_data, breadth
    ) -> tuple:
        highlights = []
        risks = []

        for ind in indices:
            chg = ind.get("chg_pct", 0)
            if chg > 1:
                highlights.append(f"{ind['name']} 涨 {chg:+.2f}%")
            elif chg < -1:
                risks.append(f"{ind['name']} 跌 {chg:+.2f}%")

        for s in sector_data[:3]:
            if s["flow"] > 0:
                highlights.append(f"{s['name']} 净流入 {s['flow']:.1f}亿")

        for s in sector_data[-3:]:
            if s["flow"] < 0:
                risks.append(f"{s['name']} 净流出 {abs(s['flow']):.1f}亿")

        if breadth:
            up_ratio = breadth.get("up_ratio", 0)
            if up_ratio < 0.3:
                risks.append(f"上涨家数仅 {breadth.get('up',0)}/{breadth.get('total',0)}")
            elif up_ratio > 0.7:
                highlights.append(f"上涨家数 {breadth.get('up',0)}/{breadth.get('total',0)}，市场情绪热烈")

        return highlights, risks

    async def _llm_review(
        self, date, market_summary, indices, sector_data,
        breadth, news_text, us_snapshot,
        portfolio_pnl, risk_level,
    ) -> str:
        llm = get_llm()

        idx_lines = []
        for ind in indices:
            idx_lines.append(f"- {ind['name']}: {ind['close']:.0f}  {ind['chg_pct']:+.2f}%")

        sector_lines = []
        for s in sector_data:
            direction = "流入" if s["flow"] > 0 else "流出"
            sector_lines.append(f"- {s['name']}: {direction} {abs(s['flow']):.1f}亿")

        NL = "\n"
        prompt = (
            f"以下是 {date} A 股盘后数据汇总，请生成一份专业的每日复盘报告。{NL}{NL}"
            f"## 市场概况{NL}{market_summary}{NL}{NL}"
            f"## 指数详情{NL}" + NL.join(idx_lines) + f"{NL}{NL}"
            f"## 板块资金流 Top{NL}" + NL.join(sector_lines[:8]) + f"{NL}{NL}"
        )

        if breadth:
            prompt += (
                f"## 市场宽度{NL}"
                f"上涨 {breadth.get('up',0)} / 下跌 {breadth.get('down',0)} / "
                f"平盘 {breadth.get('flat',0)}（上涨比例 {breadth.get('up_ratio',0):.0%}）{NL}{NL}"
            )

        if news_text:
            prompt += f"## 今日要闻{NL}{news_text[:800]}{NL}{NL}"

        if us_snapshot:
            prompt += f"## 美股收盘{NL}{us_snapshot}{NL}{NL}"

        prompt += (
            f"## 组合表现{NL}"
            f"当日盈亏 {portfolio_pnl:+,.0f}，风险等级 {risk_level}/5{NL}{NL}"
            f"请按以下结构输出复盘报告（中文，具体可操作）：{NL}"
            f"1. 市场综述（2-3 句话概括今日核心特征）{NL}"
            f"2. 板块轮动分析（资金在往哪里流？哪些板块退潮？）{NL}"
            f"3. 关键信号与风险关注{NL}"
            f"4. 明日操作策略（结合风险等级 {risk_level}/5 给出具体建议）{NL}"
            f"5. 需要关注的个股/方向（如有推荐）"
        )

        try:
            analysis = await llm.reason(
                prompt,
                system="你是一位资深 A 股投资分析师，擅长盘后综合复盘和策略制定。请用中文输出专业复盘报告。",
            )
            return analysis
        except Exception as e:
            logger.error(f"LLM 复盘失败: {e}")
            return f"[LLM 复盘暂不可用: {e}]"

    def _format_result(self, report: ReviewReport, risk_level: int) -> str:
        pnl_emoji = "🟢" if report.portfolio_pnl >= 0 else "🔴"
        lines = [
            f"## 每日复盘 — {report.date}",
            "",
            f"**市场概况**：{report.market_summary}",
            f"**组合盈亏**：{pnl_emoji} {report.portfolio_pnl:+,.0f}",
            f"**风险等级**：{risk_level}/5",
            "",
        ]

        if report.llm_analysis:
            lines.append("### AI 复盘报告")
            lines.append("")
            lines.append(report.llm_analysis)
        else:
            lines.append("### 指数表现")
            lines.append("")
            for ind in report.index_details:
                emoji = "🟢" if ind["chg_pct"] >= 0 else "🔴"
                lines.append(
                    f"- {emoji} {ind['name']}: {ind['close']:.0f} "
                    f"({ind['chg_pct']:+.2f}%)"
                )

            if report.sector_flow_top:
                lines.append("")
                lines.append("### 板块资金流")
                lines.append("")
                for s in report.sector_flow_top:
                    direction = "流入" if s["flow"] > 0 else "流出"
                    lines.append(f"- {s['name']}: {direction} {abs(s['flow']):.1f}亿")

        return "\n".join(lines)
