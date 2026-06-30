# Effective Fishstick — 项目进度归档

> 最后更新：2026-06-30

## 当前状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 项目骨架、配置系统、Agent 接口定义 | ✅ |
| Phase 2 | 数据层、选股 Agent（8 因子 + LLM） | ✅ |
| Phase 3 | 飞书 Bot 接入（Webhook + 指令解析） | ✅ |
| Phase 4 | 择时 Agent + 持仓建议 + 每日复盘 | ✅ |
| Phase 5 | 风控自适应 + 策略画像 + 交易导入 | ✅ |
| Phase 6 | 实时异动监控 + 回测框架 | ✅ |

## 已实现 Agent

| Agent | 文件 | 行数 | 功能 |
|---|---|---|---|
| StockSelector | `agents/stock_selector.py` | 278 | 8 因子 z-score 打分 + LLM 深度研判，市场状态自适应 |
| TimingAgent | `agents/timing.py` | 420 | MA/MACD/RSI/Bollinger 多周期技术指标 + LLM 语境验证 |
| PositionAdvisor | `agents/position_advisor.py` | 295 | 逐票盈亏诊断、行业集中度、LLM 再平衡建议 |
| DailyReviewer | `agents/reviewer.py` | 333 | 市场概况+板块资金流+宽度+新闻，LLM 综合复盘 |
| AnomalyMonitor | `agents/anomaly.py` | 330 | 6 条规则引擎（涨跌停/放量/换手/跳空/连涨跌）+ LLM 分级 |

## 已实现引擎

| 引擎 | 文件 | 行数 | 功能 |
|---|---|---|---|
| RiskEngine | `engine/risk.py` | 318 | 趋势/波动率/宏观/情绪四维实时打分，1-5 级风控 |
| ProfileEngine | `engine/profile.py` | 232 | YAML 画像加载/切换/注入 Agent 参数/自动匹配 |
| TradeImport | `engine/trade_import.py` | 319 | CSV 交易解析 → 行为统计 → 反推策略画像 → 导出 YAML |
| BacktestEngine | `backtest/engine.py` | 247 | 历史数据模拟 + 等权再平衡 + 沪深300 基准对比 |

## 数据源架构

```
K线/指数:   Sina 财经 JSON API (免费) → Tushare Pro (Token)
实时报价:   Sina 财经 hq.sinajs.cn (免费)  
板块/活跃:  东方财富 push2 API (免费, 已验证可用)
新闻:       东方财富 fast-news API (免费)
美股:       Tushare Pro
```

## CLI 命令

```bash
python main.py select           # 选股
python main.py timing 300099    # 技术面择时
python main.py review           # 每日复盘
python main.py risk             # 风控评估
python main.py profile          # 策略画像
python main.py anomaly 300099   # 异动监控
python main.py backtest 600519  # 回测
python main.py serve            # 飞书 Bot 服务
```

## 飞书指令

`选股` / `分析 000858` / `持仓` / `复盘` / `风险` / `异动` / `帮助`

## ⚠️ 已知问题：数据源稳定性

截至 2026-06-30，东方财富对非浏览器 TLS 指纹做了反爬封锁，
项目经过多轮调试（curl-cffi、猴子补丁、AkShare 直连）未能稳定解决。
当前方案使用 Sina 财经 JSON API 作为K线和指数的免费主数据源，
但 Sina 实时报价接口（hq.sinajs.cn）需要 Referer 校验且偶有波动。

**后续建议:**
1. 注册 Tushare Pro Token（免费），在 `config/settings.local.yaml` 配置
2. 将 Tushare 提升为主数据源，Sina 作为备源
3. 或使用付费数据服务（Wind/JoinQuant/RiceQuant）彻底解决稳定性问题
4. VPS 部署需确保能访问 `money.finance.sina.com.cn` 和 `hq.sinajs.cn`

**文件大小统计:**
- Python 源码: ~3500 行
- 单测: 21 个, 全部通过
- Git 提交: 20+ 次
