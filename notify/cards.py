"""飞书消息卡片模板构建器。

飞书卡片 JSON 格式参考：https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/feishu-cards/card-components
"""

from datetime import datetime

# ── 主题色 ──────────────────────────────────────────────────

COLOR_BLUE = "blue"
COLOR_RED = "red"
COLOR_GREEN = "green"
COLOR_ORANGE = "orange"
COLOR_GREY = "grey"

HEADER_TITLE_TAG = "plain_text"


def _header(title: str, color: str = COLOR_BLUE) -> dict:
    return {
        "title": {"tag": HEADER_TITLE_TAG, "content": title},
        "template": color,
    }


def _markdown(content: str) -> dict:
    return {"tag": "markdown", "content": content}


def _hr() -> dict:
    return {"tag": "hr"}


def _note(*elements) -> dict:
    return {"tag": "note", "elements": list(elements)}


def _action(actions: list[dict]) -> dict:
    return {"tag": "action", "actions": actions}


def _button(text: str, value: dict | None = None, button_type: str = "primary") -> dict:
    btn = {
        "tag": "button",
        "text": {"tag": "plain_text", "content": text},
        "type": button_type,
    }
    if value:
        btn["value"] = value
    return btn


def _column_set(columns: list) -> dict:
    """多列布局，每个 column 是元素列表。"""
    return {
        "tag": "column_set",
        "flex_mode": "bisect",
        "background_style": "default",
        "column_width_mode": "auto",
        "columns": [
            {"tag": "column", "elements": col, "width": "weighted", "weight": 1}
            for col in columns
        ],
    }


def _field(text: str) -> dict:
    """单行文本（短），常用于 column 内。"""
    return {"tag": "div", "text": {"tag": "plain_text", "content": text}}
    # 用 div 而不是 lark_md 确保文本短小


def _table_row(cells: list[str]) -> list[dict]:
    return [{"tag": "div", "text": {"tag": "plain_text", "content": c}} for c in cells]


# ── 业务卡片 ────────────────────────────────────────────────

def build_stock_selection_card(
    top_stocks: list[dict],
    market_context: str,
    regime: str,
    screened: int = 0,
    passed: int = 0,
    llm_analysis: str = "",
) -> dict:
    """选股结果卡片。

    top_stocks: [{"code", "name", "score", "pe", "pb", "inflow", "turnover"}, ...]
    """
    regime_cn = "🛡️ 防御模式" if regime == "defensive" else "📊 正常模式"
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    # header
    elements = [
        _markdown(f"**筛选范围** {screened} → {passed} 只通过  |  {market_context}"),
        _markdown(f"**因子模式** {regime_cn}  |  {date_str}"),
        _hr(),
    ]

    # ranking list — markdown 表格
    table_lines = ["**Top 候选**\n"]
    for i, s in enumerate(top_stocks[:10]):
        code = s.get("code", "")
        name = s.get("name", "")
        score = s.get("score", 0)
        pe = s.get("pe", 0)
        inflow = s.get("inflow", 0) or 0
        table_lines.append(f"{i+1}. {code} {name} 得分 {score:.3f}  PE {pe:.1f}  主力净流入 {inflow:.0f}万")
    elements.append(_markdown("\n".join(table_lines)))

    if llm_analysis:
        elements.append(_hr())
        # 限制分析长度，避免卡片过大
        truncated = llm_analysis[:1200]
        if len(llm_analysis) > 1200:
            truncated += "\n\n...（内容过长已截断）"
        elements.append(_markdown(truncated))

    elements.append(_hr())
    elements.append(_note(
        {"tag": "plain_text", "content": "💡 发送「分析 代码」查看单票深度分析"}
    ))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header("🔍 今日选股", COLOR_BLUE),
        "elements": elements,
    }


def build_anomaly_alert_card(
    code: str,
    name: str,
    alert_type: str,
    severity: str,
    detail: str,
    timestamp: str = "",
) -> dict:
    """异动告警卡片。"""
    severity_map = {
        "high": ("🚨", COLOR_RED),
        "medium": ("⚠️", COLOR_ORANGE),
        "low": ("ℹ️", COLOR_GREY),
    }
    icon, color = severity_map.get(severity, ("🔔", COLOR_BLUE))
    time_str = timestamp or datetime.now().strftime("%H:%M:%S")

    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"{icon} 异动告警 — {name}({code})", color),
        "elements": [
            _markdown(f"**类型** {alert_type}  |  **严重等级** {severity.upper()}  |  {time_str}"),
            _hr(),
            _markdown(detail),
            _hr(),
            _action([
                _button("📊 分析", {"cmd": "analyze", "code": code}),
                _button("📋 复盘", {"cmd": "review"}),
            ]),
        ],
    }


def build_position_card(
    positions: list[dict],
    total_value: float = 0,
    risk_level: int = 3,
) -> dict:
    """持仓分析卡片。"""
    elements = [
        _markdown(f"**风险等级** {risk_level}/5  |  **总市值** {total_value:,.0f}"),
        _hr(),
    ]

    if positions:
        lines = ["**当前持仓**\n"]
        for i, p in enumerate(positions):
            code = p.get("code", "")
            name = p.get("name", "")
            weight = p.get("weight", 0)
            pnl = p.get("pnl_pct", 0)
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(f"{i+1}. {code} {name} 仓位 {weight:.1%}  {emoji} {pnl:+.2%}")
        elements.append(_markdown("\n".join(lines)))
    else:
        elements.append(_markdown("暂无持仓数据"))

    elements.append(_hr())
    elements.append(_note({"tag": "plain_text", "content": "💡 发送「持仓」查看详细诊断"}))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header("💼 持仓概览", COLOR_GREEN),
        "elements": elements,
    }


def build_review_card(
    date: str,
    market_summary: str,
    portfolio_pnl: float = 0,
    highlights: list[str] | None = None,
    risks: list[str] | None = None,
) -> dict:
    """每日复盘卡片。"""
    pnl_color = COLOR_GREEN if portfolio_pnl >= 0 else COLOR_RED
    pnl_sign = "+" if portfolio_pnl >= 0 else ""

    elements = [
        _markdown(f"**日期** {date}  |  **组合收益** {pnl_sign}{portfolio_pnl:.2%}"),
        _markdown(f"**市场概况** {market_summary}"),
        _hr(),
    ]

    if highlights:
        elements.append(_markdown("**亮点**\n" + "\n".join(f"• {h}" for h in highlights)))
    if risks:
        elements.append(_markdown("**风险关注**\n" + "\n".join(f"• {r}" for r in risks)))

    elements.append(_hr())
    elements.append(_action([
        _button("🔍 选股", {"cmd": "select"}, "primary"),
        _button("📊 持仓", {"cmd": "position"}, "default"),
    ]))

    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"📋 每日复盘 — {date}", pnl_color),
        "elements": elements,
    }


def build_help_card() -> dict:
    """帮助 / 指令参考卡片。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": _header("🤖 Effective Fishstick 指令", COLOR_BLUE),
        "elements": [
            _markdown(
                "**选股指令**\n"
                "• `选股` — 触发全市场多因子选股\n"
                "• `选股 半导体` — 按行业过滤\n\n"
                "**分析指令**\n"
                "• `分析 600519` — 单票深度分析\n"
                "• `分析 600519 5日` — 指定时间窗口\n\n"
                "**持仓指令**\n"
                "• `持仓` — 持仓诊断与再平衡建议\n\n"
                "**复盘指令**\n"
                "• `复盘` — 当日盘后综合复盘\n\n"
                "**风控指令**\n"
                "• `风险 3` — 手动锁定风险等级（1-5）\n"
                "• `风险` — 查看当前风险等级"
            ),
            _hr(),
            _note({"tag": "plain_text", "content": "💡 发送「帮助」随时查看此菜单"}),
        ],
    }


def build_loading_card(action: str) -> dict:
    """处理中提示卡片。"""
    return {
        "config": {"wide_screen_mode": True},
        "header": _header(f"⏳ {action} — 处理中...", COLOR_GREY),
        "elements": [
            _markdown(f"正在执行 **{action}**，请稍候..."),
        ],
    }


def build_error_card(title: str, detail: str = "") -> dict:
    """错误提示卡片。"""
    elements = [_markdown(f"**{title}**")]
    if detail:
        elements.append(_markdown(detail))
    return {
        "config": {"wide_screen_mode": True},
        "header": _header("❌ 操作失败", COLOR_RED),
        "elements": elements,
    }
