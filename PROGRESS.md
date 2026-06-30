# Effective Fishstick — 项目进度归档

> 最后更新：2026-06-30

## 当前状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 项目骨架、配置系统、Agent 接口定义 | ✅ |
| Phase 2 | 数据层（AkShare）、选股 Agent（8 因子 + LLM） | ✅ |
| Phase 3 | 飞书 Bot 接入（Webhook + 指令解析） | ✅ |
| Phase 4 | 择时 Agent + 持仓建议 + 每日复盘 | ✅ |
| Phase 5 | 风控自适应 + 策略画像 + 交易导入 | ✅ |
| Phase 6 | 实时异动监控 + 回测框架 | 下一步 |

## 已实现 Agent

| Agent | 文件 | 行数 | 功能 |
|---|---|---|---|
| StockSelector | `agents/stock_selector.py` | 278 | 8 因子 z-score 打分 + LLM 深度研判，支持市场状态自适应 |
| TimingAgent | `agents/timing.py` | 420 | MA/MACD/RSI/Bollinger 多周期技术指标 + LLM 语境验证 |
| PositionAdvisor | `agents/position_advisor.py` | 304 | 逐票盈亏诊断、行业集中度分析、LLM 再平衡建议 |
| DailyReviewer | `agents/reviewer.py` | 333 | 市场概况+板块资金流+宽度+新闻，LLM 综合复盘 |

## 已实现引擎

| 引擎 | 文件 | 行数 | 功能 |
|---|---|---|---|
| RiskEngine | `engine/risk.py` | 265 | 趋势/波动率/宏观/情绪四维实时打分，1-5 级风控等级 |
| ProfileEngine | `engine/profile.py` | 210 | YAML 画像加载/切换/注入 Agent 参数/自动匹配 |
| TradeImport | `engine/trade_import.py` | 290 | CSV 交易解析 → 行为统计 → 反推策略画像 → 导出 YAML |

## 关键架构决策

- LLM: DeepSeek API（chat=flash 筛查，reasoner=pro 深度分析）
- Agent 框架: 自研轻量 Orchestrator（DAG 调度），不引入 CrewAI/AutoGen
- 数据: AkShare（免费主力）+ 东方财富 push2 API（精选池）
- 缓存: Parquet + MD5 键值 + TTL 过期
- 通知: 飞书 Webhook + Lark Open API（消息卡片+指令解析）
- 风控: 四维综合评分 → 1-5 级 → 仓位上限/止损宽度
- 策略画像: 3 套预置模板（default/buffett_value/momentum）+ CSV 反推

## 飞书指令

- `选股` / `选股 半导体` → StockSelector
- `分析 600519` → TimingAgent 单票技术面
- `持仓` → PositionAdvisor
- `复盘` → DailyReviewer
- `风险` / `风险 3` / `风险 0` → RiskEngine
- `帮助` → 指令菜单

## 命令行入口

```bash
python main.py select          # 选股
python main.py timing 600519   # 单票技术分析
python main.py review          # 每日复盘
python main.py risk            # 风险评估
python main.py profile         # 查看/切换策略画像
python main.py import trades.csv  # 导入交易记录
python main.py serve           # 启动飞书 Bot 服务
```

## 未实现/Stub

| 模块 | 状态 |
|---|---|
| `agents/anomaly.py` | 8 行 stub，Phase 6 |
| `storage/models.py` | 2 行 stub |
| `storage/vector_store.py` | 2 行 stub |

## 下一步

Phase 6: AnomalyMonitor 实时异动监控（规则引擎+LLM分级）+ 回测框架集成
