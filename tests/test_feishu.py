"""飞书相关模块测试：指令解析、卡片构建。"""

from notify.commands import parse_command
from notify.cards import (
    build_stock_selection_card,
    build_anomaly_alert_card,
    build_position_card,
    build_review_card,
    build_help_card,
    build_loading_card,
    build_error_card,
)


class TestCommandParser:

    def test_select(self):
        cmd = parse_command("选股")
        assert cmd.action == "select"
        assert cmd.args == {}

    def test_select_sector(self):
        cmd = parse_command("选股 半导体")
        assert cmd.action == "select"
        assert cmd.args["sector"] == "半导体"

    def test_analyze(self):
        cmd = parse_command("分析 600519")
        assert cmd.action == "analyze"
        assert cmd.args["code"] == "600519"

    def test_analyze_with_days(self):
        cmd = parse_command("分析 600519 5日")
        assert cmd.action == "analyze"
        assert cmd.args["days"] == 5

    def test_position(self):
        cmd = parse_command("持仓")
        assert cmd.action == "position"

    def test_review(self):
        cmd = parse_command("复盘")
        assert cmd.action == "review"

    def test_risk(self):
        cmd = parse_command("风险")
        assert cmd.action == "risk"
        assert cmd.args["level"] is None

    def test_risk_level(self):
        cmd = parse_command("风险 3")
        assert cmd.action == "risk"
        assert cmd.args["level"] == 3

    def test_help(self):
        cmd = parse_command("帮助")
        assert cmd.action == "help"

    def test_raw_code(self):
        cmd = parse_command("600519")
        assert cmd.action == "analyze"
        assert cmd.args["code"] == "600519"

    def test_unknown(self):
        assert parse_command("你好").action == "unknown"
        assert parse_command("").action == "unknown"


class TestCards:

    def test_stock_selection_card(self):
        stocks = [{"code": "000001", "name": "平安", "score": 0.85, "pe": 5.2, "pb": 0.7, "inflow": 12000, "turnover": 2.3}]
        card = build_stock_selection_card(stocks, "上证 3200 (+0.5%)", "normal", 100, 50)
        assert card["header"]["title"]["content"] == "🔍 今日选股"
        assert len(card["elements"]) > 0

    def test_stock_selection_defensive(self):
        stocks = [{"code": "000001", "name": "平安", "score": 0.7, "pe": 5.0, "pb": 0.7, "inflow": 5000, "turnover": 1.5}]
        card = build_stock_selection_card(stocks, "创业板 -3.0%", "defensive", 100, 30)
        assert "防御" in str(card["elements"])

    def test_anomaly_alert_card(self):
        card = build_anomaly_alert_card("000001", "平安", "异常放量", "high", "成交量大增")
        assert "异动告警" in card["header"]["title"]["content"]
        assert card["header"]["template"] == "red"

    def test_position_card(self):
        card = build_position_card([{"code": "000001", "name": "平安", "weight": 0.3, "pnl_pct": 0.05}], 100000)
        assert card["header"]["title"]["content"] == "💼 持仓概览"

    def test_review_card(self):
        card = build_review_card("2026-06-27", "上证 +0.5%", 0.02, ["选中涨停"], ["外围风险"])
        assert "每日复盘" in card["header"]["title"]["content"]

    def test_help_card(self):
        card = build_help_card()
        assert "Effective Fishstick" in card["header"]["title"]["content"]

    def test_loading_card(self):
        card = build_loading_card("选股")
        assert "处理中" in card["header"]["title"]["content"]

    def test_error_card(self):
        card = build_error_card("错误", "详情")
        assert card["header"]["template"] == "red"

    def test_llm_truncation(self):
        stocks = [{"code": "000001", "name": "平安", "score": 0.8, "pe": 5.0, "pb": 0.7, "inflow": 1000, "turnover": 1.0}]
        card = build_stock_selection_card(stocks, "上证", "normal", 100, 50, llm_analysis="X" * 2000)
        assert "已截断" in str(card["elements"])
