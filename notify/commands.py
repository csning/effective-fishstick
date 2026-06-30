"""飞书指令解析器 — 将用户消息文本解析为结构化指令对象。"""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedCommand:
    """解析后的飞书指令。"""

    action: str                                # select, analyze, position, review, risk, help, unknown
    raw: str = ""                              # 原始消息文本
    args: dict = field(default_factory=dict)   # 附加参数


# ── 指令匹配规则 ────────────────────────────────────────────

# 规则按优先级排列：(正则, action, 参数提取函数)
RULES: list[tuple[re.Pattern, str, callable]] = []


def _register(pattern: str, action: str, extractor=None):
    RULES.append((re.compile(pattern), action, extractor or (lambda m: {})))


# 选股: 选股 / 选股 半导体 / select
_register(r"^(选股|select|stock)\s*(.*)$", "select",
          lambda m: {"sector": m.group(2).strip()} if m.group(2).strip() else {})

# 分析: 分析 600519 / 分析 600519 5日 / analyze
_register(r"^(分析|analyze)\s+(\d{6})(?:\s+(\d+)日)?\s*$", "analyze",
          lambda m: {"code": m.group(2), "days": int(m.group(3)) if m.group(3) else 20})

# 持仓: 持仓 / position / portfolio
_register(r"^(持仓|position|portfolio)$", "position")

# 复盘: 复盘 / review
_register(r"^(复盘|review)$", "review")

# 风险: 风险 / 风险 3 / risk
_register(r"^(风险|risk)(?:\s+([1-5]))?\s*$", "risk",
          lambda m: {"level": int(m.group(2)) if m.group(2) else None})
_register(r"^(异动|anomaly|monitor)\s*$", "anomaly")


# 帮助: 帮助 / help
_register(r"^(帮助|help|\?)$", "help")


def parse_command(text: str) -> ParsedCommand:
    """将用户消息文本解析为 ParsedCommand。"""
    stripped = text.strip()
    if not stripped:
        return ParsedCommand(action="unknown", raw=text)

    for pattern, action, extractor in RULES:
        m = pattern.match(stripped)
        if m:
            return ParsedCommand(action=action, raw=stripped, args=extractor(m))

    # 默认：尝试匹配 6 位数字 — 可能是直接发了一个股票代码
    code_match = re.match(r"^(\d{6})$", stripped)
    if code_match:
        return ParsedCommand(action="analyze", raw=stripped, args={"code": code_match.group(1)})

    return ParsedCommand(action="unknown", raw=stripped)
