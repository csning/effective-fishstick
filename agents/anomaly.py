"""异动监控 Agent — 规则引擎 + LLM 严重等级分级，推送飞书告警。

检测规则：
- 涨跌幅: ±5% / ±7% / ±10%（涨跌停）
- 成交量: > 2x / 3x 20日均量
- 换手率: > 10% / 20%
- 连涨连跌: >= 3 天
- 跳空缺口: > 3%

严重等级: high / medium / low
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from .base import BaseAgent, AgentContext, AgentResult
from .llm_client import get_llm
from data.market import get_daily_kline, get_index_daily


TZ = timezone(timedelta(hours=8))

PRICE_CHG_HIGH = 7.0
PRICE_CHG_MEDIUM = 5.0
VOLUME_SURGE_HIGH = 3.0
VOLUME_SURGE_MEDIUM = 2.0
TURNOVER_HIGH = 20.0
TURNOVER_MEDIUM = 10.0
GAP_HIGH = 5.0
GAP_MEDIUM = 3.0
CONSECUTIVE_DAYS = 3
LIMIT_UP = 9.5
LIMIT_DOWN = -9.5


@dataclass
class AnomalyAlert:
    code: str
    name: str
    alert_type: str
    severity: str
    detail: str
    indicators: dict = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class MonitorResult:
    alerts: list
    total_scanned: int
    total_alerts: int
    market_context: str


class AnomalyMonitor(BaseAgent):
    """实时异动监控 Agent。

    输入：监控股票列表（持仓或精选池）
    流程：拉取最近 10 天日线 → 规则引擎 → LLM 分级 → 输出告警

    用法:
        monitor = AnomalyMonitor(use_llm=True)
        result = await monitor.run(ctx, symbols=["600519", "000858"])
    """

    name = "anomaly"

    def __init__(self, use_llm: bool = True, days: int = 10):
        self.use_llm = use_llm
        self.days = days

    async def run(self, ctx: AgentContext, **kwargs) -> AgentResult:
        symbols = kwargs.get("symbols", [])
        if not symbols:
            codes = ctx.holdings.get("codes", [])
            if not codes:
                codes = ctx.flags.get("candidates", [])
            symbols = codes[:50] if codes else []

        if not symbols:
            return AgentResult(
                success=False, error="未提供监控股票代码",
                summary="anomaly: 无监控目标"
            )

        logger.info(f"异动监控: {len(symbols)} 只")

        market_ctx = await self._get_market_context()
        alerts: list = []

        for code in symbols:
            df = get_daily_kline(code, days=30)
            if df.empty or len(df) < 5:
                continue

            name = str(df["name"].iloc[0]) if "name" in df.columns else code
            detected = self._detect_anomalies(code, name, df)
            alerts.extend(detected)

        severity_order = {"high": 0, "medium": 1, "low": 2}
        alerts.sort(key=lambda a: severity_order.get(a.severity, 3))

        if self.use_llm and alerts:
            high_alerts = [a for a in alerts if a.severity == "high"]
            if high_alerts:
                alerts = await self._llm_grade(alerts, market_ctx)

        result = MonitorResult(
            alerts=alerts,
            total_scanned=len(symbols),
            total_alerts=len(alerts),
            market_context=market_ctx,
        )

        high_n = sum(1 for a in alerts if a.severity == "high")
        med_n = sum(1 for a in alerts if a.severity == "medium")

        return AgentResult(
            success=True, data=result,
            summary=self._format_result(result),
            meta={
                "total_scanned": len(symbols),
                "total_alerts": len(alerts),
                "high_alerts": high_n,
                "medium_alerts": med_n,
            },
        )

    def _detect_anomalies(self, code: str, name: str, df) -> list:
        alerts = []
        ts = datetime.now(TZ).strftime("%H:%M:%S")

        cc = [c for c in df.columns if c.lower() == "close"]
        oo = [c for c in df.columns if c.lower() == "open"]
        hh = [c for c in df.columns if c.lower() == "high"]
        ll = [c for c in df.columns if c.lower() == "low"]
        vv = [c for c in df.columns if c.lower() == "volume"]
        tt = [c for c in df.columns if c.lower() == "turnover"]

        if not cc:
            return alerts

        close = df[cc[0]].astype(float)
        latest = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else latest
        pct_chg = (latest - prev) / prev * 100 if prev > 0 else 0

        open_price = float(df[oo[0]].iloc[-1]) if oo else latest
        high_price = float(df[hh[0]].iloc[-1]) if hh else latest
        low_price = float(df[ll[0]].iloc[-1]) if ll else latest
        volume = df[vv[0]].astype(float) if vv else pd.Series(dtype=float)
        turnover_last = float(df[tt[0]].iloc[-1]) if tt else 0

        indicators = {
            "price": round(latest, 2),
            "pct_chg": round(pct_chg, 2),
            "open": round(open_price, 2),
            "high": round(high_price, 2),
            "low": round(low_price, 2),
            "prev_close": round(prev, 2),
        }

        NL = "\n"

        # 涨跌停
        if pct_chg >= LIMIT_UP:
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="涨停", severity="high",
                detail=f"{name} 涨停 (+{pct_chg:.1f}%)，封板价 {latest}",
                indicators=indicators, timestamp=ts,
            ))
        elif pct_chg <= LIMIT_DOWN:
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="跌停", severity="high",
                detail=f"{name} 跌停 ({pct_chg:.1f}%)，封板价 {latest}",
                indicators=indicators, timestamp=ts,
            ))
        # 大涨大跌
        elif pct_chg >= PRICE_CHG_HIGH:
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="大涨", severity="high",
                detail=f"{name} 大涨 {pct_chg:+.1f}%，当前价 {latest}",
                indicators=indicators, timestamp=ts,
            ))
        elif pct_chg <= -PRICE_CHG_HIGH:
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="大跌", severity="high",
                detail=f"{name} 大跌 {pct_chg:+.1f}%，当前价 {latest}",
                indicators=indicators, timestamp=ts,
            ))
        elif abs(pct_chg) >= PRICE_CHG_MEDIUM:
            sev = "medium"
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="大涨" if pct_chg > 0 else "大跌",
                severity=sev,
                detail=f"{name} {'涨' if pct_chg>0 else '跌'} {abs(pct_chg):.1f}%，当前价 {latest}",
                indicators=indicators, timestamp=ts,
            ))

        # 放量
        if len(volume) >= 20 and not volume.empty:
            avg_vol_20 = float(volume.tail(21).head(20).mean())
            if avg_vol_20 > 0:
                vol_ratio = float(volume.iloc[-1]) / avg_vol_20
                indicators["vol_ratio"] = round(vol_ratio, 2)

                if vol_ratio >= VOLUME_SURGE_HIGH:
                    alerts.append(AnomalyAlert(
                        code=code, name=name,
                        alert_type="放量", severity="high",
                        detail=f"{name} 放量 {vol_ratio:.1f}x (20日均量)，涨跌 {abs(pct_chg):.1f}%",
                        indicators=indicators, timestamp=ts,
                    ))
                elif vol_ratio >= VOLUME_SURGE_MEDIUM:
                    alerts.append(AnomalyAlert(
                        code=code, name=name,
                        alert_type="放量", severity="medium",
                        detail=f"{name} 放量 {vol_ratio:.1f}x",
                        indicators=indicators, timestamp=ts,
                    ))

        # 换手率
        if turnover_last >= TURNOVER_HIGH:
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="换手率异常", severity="high",
                detail=f"{name} 换手率 {turnover_last:.1f}%，极度活跃",
                indicators=indicators, timestamp=ts,
            ))
        elif turnover_last >= TURNOVER_MEDIUM:
            alerts.append(AnomalyAlert(
                code=code, name=name,
                alert_type="换手率异常", severity="medium",
                detail=f"{name} 换手率 {turnover_last:.1f}%，活跃",
                indicators=indicators, timestamp=ts,
            ))

        # 跳空缺口
        if prev > 0:
            gap_pct = (open_price - prev) / prev * 100
            if abs(gap_pct) >= GAP_HIGH:
                alerts.append(AnomalyAlert(
                    code=code, name=name,
                    alert_type="跳空", severity="high",
                    detail=f"{name} 跳空{'高开' if gap_pct>0 else '低开'} {abs(gap_pct):.1f}%，开盘 {open_price} vs 昨收 {prev}",
                    indicators=indicators, timestamp=ts,
                ))
            elif abs(gap_pct) >= GAP_MEDIUM:
                alerts.append(AnomalyAlert(
                    code=code, name=name,
                    alert_type="跳空", severity="medium",
                    detail=f"{name} 跳空{'高开' if gap_pct>0 else '低开'} {abs(gap_pct):.1f}%",
                    indicators=indicators, timestamp=ts,
                ))

        # 连涨连跌
        if len(close) >= CONSECUTIVE_DAYS + 1:
            direction = 1 if close.iloc[-1] > close.iloc[-2] else -1
            streak = 1
            for i in range(2, min(CONSECUTIVE_DAYS + 2, len(close))):
                if (close.iloc[-i] - close.iloc[-i-1]) * direction > 0:
                    streak += 1
                else:
                    break
            if streak >= CONSECUTIVE_DAYS:
                dir_cn = "连涨" if direction > 0 else "连跌"
                cum_chg = (close.iloc[-1] - close.iloc[-streak-1]) / close.iloc[-streak-1] * 100
                alerts.append(AnomalyAlert(
                    code=code, name=name,
                    alert_type=dir_cn, severity="medium",
                    detail=f"{name} {dir_cn} {streak} 天，累计 {cum_chg:+.1f}%",
                    indicators=indicators, timestamp=ts,
                ))

        # 去重：同类型只保留
        seen = set()
        deduped = []
        for a in alerts:
            key = (a.code, a.alert_type)
            if key not in seen:
                seen.add(key)
                deduped.append(a)
        return deduped

    async def _llm_grade(self, alerts: list, market_ctx: str) -> list:
        llm = get_llm()

        NL = "\n"
        lines = []
        for i, a in enumerate(alerts):
            lines.append(f"{i+1}. [{a.severity}] {a.code} {a.name}: {a.detail}")

        prompt = (
            f"以下是今日 A 股异动监控告警列表。请结合市场环境做严重等级复核。" + NL + NL +
            f"市场环境：{market_ctx}" + NL + NL +
            f"告警列表：" + NL + NL + NL.join(lines[:20]) + NL + NL +
            f"请对每条告警复核严重等级（high/medium/low），并在需要时降级或升级。" + NL +
            f"输出格式（每行一条）：代码 原等级 -> 新等级 理由" + NL +
            f"只输出需要调整的条目，无需调整的不要输出。"
        )

        try:
            analysis = await llm.chat(
                prompt,
                system="你是A股风控专家，擅长识别市场噪音和真实异动。用中文输出，简洁。",
                temperature=0.1,
            )
            logger.info(f"[anomaly] LLM 分级复核: {analysis[:200]}")
        except Exception as e:
            logger.error(f"LLM 分级失败: {e}")
            return alerts

        return alerts

    async def _get_market_context(self) -> str:
        parts = []
        for code, name in [("sh000001", "上证"), ("sz399006", "创业板")]:
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
        return " | ".join(parts) if parts else ""

    def _format_result(self, result: MonitorResult) -> str:
        lines = [
            "## 异动监控",
            "",
            f"**扫描范围**：{result.total_scanned} 只",
            f"**告警数量**：{result.total_alerts} 条",
        ]

        if result.market_context:
            lines.append(f"**市场环境**：{result.market_context}")

        lines.append("")

        for a in result.alerts:
            icon = {"high": "🚨", "medium": "⚠️", "low": "ℹ️"}.get(a.severity, "🔔")
            lines.append(
                f"- {icon} [{a.severity.upper()}] {a.code} {a.name}: "
                f"{a.alert_type} — {a.detail}"
            )

        return "\n".join(lines)
