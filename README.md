# Effective Fishstick（有效鱼竿）

AI 驱动的 A 股量化交易助手。多智能体协作架构，覆盖选股、择时、仓位管理、异动监控与每日复盘，通过飞书交付。

## 系统架构

```
飞书 / 微信（通知推送 + 指令触发）
       │
  VPS (2C4G) — 在线层
  │-- 飞书 Bot（Webhook + 指令解析）
  │-- Redis Pub/Sub（消息总线、任务队列）
  │-- APScheduler（定时任务：每日复盘、开盘/收盘例程）
  │-- Nginx（反向代理）
       │  Tailscale/WireGuard 内网穿透
  Mac Mini M4 — 算力层
  │-- Orchestrator（Agent DAG 调度器）
  │-- Agent：选股、择时、持仓建议、异动监控、每日复盘
  │-- 风控引擎（1-5 级自适应，飞书指令手动覆写）
  │-- 数据层：AkShare（A 股行情/财务/新闻）+ Tushare Pro（分钟线）
  │             ChromaDB（历史行情模式相似度检索）
  │-- LLM：DeepSeek（chat 日常筛查 + reasoner 深度分析）
  │-- 策略画像（YAML 交易偏好模板）
```

## 功能模块

| 功能 | 对应 Agent | 触发方式 |
|---|---|---|
| 选股建议 | `StockSelector` | 盘前定时 / 飞书指令 |
| 持仓建议 | `PositionAdvisor` | 按需 / 盘前 |
| 买卖时机 | `TimingAgent` | 盘中信号 + LLM 语境判断 |
| 异动提醒 | `AnomalyMonitor` | 实时 WebSocket 推送 |
| 每日复盘 | `DailyReviewer` | 盘后定时 |
| 风控自适应 | `RiskEngine` | 每日开盘扫描 |
| 策略画像 | `profiles/*.yaml` | 手动切换 / 自动匹配 |

### 飞书指令（规划中）

| 指令 | 作用 |
|---|---|
| `选股` / `选股 半导体` | 触发选股，可选行业过滤 |
| `分析 600519` | 单票深度分析 |
| `持仓` | 持仓诊断 |
| `复盘` | 触发当日复盘 |
| `风险 3` | 手动锁定风险等级 |

## 技术栈

| 层级 | 选型 | 说明 |
|---|---|---|
| 语言 | Python 3.12+ | |
| LLM | DeepSeek（chat + reasoner 混用） | chat 做筛查，reasoner 做深度研判 |
| Agent 框架 | 自研轻量 Orchestrator | 不引入 CrewAI/AutoGen 等重量框架 |
| 行情数据 | AkShare（免费）+ Tushare Pro（付费） | A 股日线/分钟线/财务/新闻 |
| 向量数据库 | ChromaDB | 历史模式相似度检索 |
| 消息队列 | Redis Pub/Sub | Agent 间解耦 + VPS-Mac 桥接 |
| 调度 | APScheduler | 定时任务 |
| 存储 | SQLite + Parquet | 后续可迁 PostgreSQL |
| 通知 | 飞书自定义应用（Lark Open API） | 消息卡片 + 交互指令 |

## 项目结构

```
effective-fishstick/
├── config/                 # 配置层：YAML + Pydantic 模型
│   ├── settings.py         #   LLM、数据、风控、通知、VPS 配置模型
│   ├── settings.yaml       #   默认值（环境变量 / settings.local.yaml 覆盖）
│   └── settings.local.yaml #   git-ignored，存放 API key、token 等敏感信息
├── data/                   # 数据层
│   ├── cache.py            #   Parquet 缓存（TTL 过期 + MD5 键值）
│   ├── market.py           #   A 股日线、指数、美股三大指数、行业资金流
│   ├── fundamental.py      #   全市场快照（PE/PB/ROE/市值）+ 个股财报摘要
│   └── news.py             #   东方财富新闻 + 公司公告
├── agents/                 # 多 Agent 协作层
│   ├── base.py             #   BaseAgent 抽象 + AgentContext + AgentResult
│   ├── orchestrator.py     #   轻量 Pipeline DAG 调度器
│   ├── llm_client.py       #   DeepSeek API 封装（chat + reason 双模式）
│   ├── stock_selector.py   #   多因子选股（8 因子 z-score）+ LLM 深度分析
│   ├── position_advisor.py #   持仓再平衡建议（stub）
│   ├── timing.py           #   技术信号 + LLM 语境验证（stub）
│   ├── anomaly.py          #   规则引擎 + LLM 严重等级分级（stub）
│   └── reviewer.py         #   盘后结构化复盘报告（stub）
├── engine/                 # 决策执行层
│   └── risk.py             #   风控引擎：综合评分 → 等级映射 → 仓位/止损计算
├── notify/                 # 通知推送
│   └── feishu.py           #   飞书通知器：文本 / 卡片 / 告警三级推送
├── storage/                # 持久化
│   ├── models.py           #   ORM 模型（仓位、信号、复盘日志）
│   └── vector_store.py     #   ChromaDB 行情快照存储
├── profiles/               # 策略画像模板
│   ├── default.yaml        #   默认平衡型
│   ├── buffett_value.yaml  #   巴菲特价值投资倾向
│   └── momentum.yaml       #   趋势动量倾向
├── web/                    # 预留：Web 管理面板
├── tests/
└── main.py                 # 入口
```

## 快速开始

```bash
# 创建虚拟环境
python3.12 -m venv .venv && source .venv/bin/activate

# 安装依赖
pip install -e ".[dev]"

# 配置 DeepSeek API key（已写入 settings.local.yaml）
# 如需 Tushare token：
echo 'data:\n  tushare_token: "your-token"' >> config/settings.local.yaml

# 运行占位指令
python main.py
```

## 设计决策

### 为什么不引入 CrewAI / AutoGen / Hermes？

五个 Agent 构成简单的 DAG 拓扑（选股 → 择时 → 仓位 → 风控，复盘为独立分支），不是网状交互。
自研轻量 Orchestrator 约 150 行，Agent 间通过结构化数据传递，更易调试且无框架锁定。
当 Agent 交互模式变得非线性时再重新评估。

### DeepSeek 双模型策略

- `deepseek-chat`：快速、便宜 — 用于量化筛查、新闻摘要、日常任务。
- `deepseek-reasoner`：强推理 — 用于复杂分析：语境信号验证、每日复盘综合、黑天鹅解读。

### 硬件分工：VPS + Mac Mini

VPS（2C4G）负责 7×24 在线：飞书 Webhook、Redis、轻量调度。
Mac Mini M4 负责算力密集型任务：LLM 推理、回测、批量数据处理。
通过 Tailscale 组内网，无需开放公网端口。

### 风控自适应

综合评分（趋势 × 0.35 + 波动率倒数 × 0.25 + 宏观 × 0.20 + 情绪 × 0.20）映射到 1-5 级。
每级控制仓位上限和止损宽度。可通过飞书 `风险 N` 指令或手动配置覆写。

### 策略画像与交易行为导入

不直接导入原始成交记录，而是将交易行为抽象为策略画像（PE 上限、持仓周期、行业偏好、止损策略等）。
画像参数注入 Agent 的 prompt 和风控计算。后续可从实际交易 CSV 反推画像，实现自动生成。

## 待定事项（建设中逐步敲定）

1. DeepSeek 双模型混用比例：初步 chat 70% / reasoner 30%，随实测调整。
2. A 股为主（已开通创业板 + 可转债权限），每日分析参考美股整体走势。
3. 飞书接入方式：自定义应用（Lark Open API），支持消息卡片和交互指令。
4. 交易执行：纯模拟 + 回测，参考后手动实盘交易。
5. 大佬交易数据：碎片化社交动态为主（NLP 解析），非结构化 CSV。
6. LLM 全部走 DeepSeek 远端，不部署本地模型。


## ⚠️ 已知问题

> 数据源稳定性：东方财富对非浏览器 TLS 指纹做反爬封锁，curl-cffi 未能稳定绕过。
> 当前方案使用 **Sina 财经 JSON API** 作为 K 线/指数/实时报价的免费主数据源，
> 有 Referer 校验且偶有波动。建议注册 Tushare Pro Token（免费）作为备源。
> 详见 [PROGRESS.md](PROGRESS.md) 的已知问题章节。

## 数据源

| 平台 | 类型 | 注册 | 用途 |
|---|---|---|---|
| AkShare | 免费 | 无需 | 主力数据源：日线、指数、财务、新闻、可转债 |
| Tushare Pro | 免费/积分制 | [tushare.pro](https://tushare.pro) | 分钟线、因子数据、港股通 |
| 聚宽 | 免费额度 | [joinquant.com](https://www.joinquant.com) | 因子数据、回测框架 |
| 米筐 | 免费额度 | [ricequant.com](https://www.ricequant.com) | 财务数据、因子库 |

## 版本路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| Phase 1 | 项目骨架、配置系统、Agent 接口定义 | ✅ |
| Phase 2 | 数据层（AkShare）、选股 Agent（8 因子 + LLM） | ✅ |
| Phase 3 | 飞书 Bot 接入（Webhook + 指令解析） | 待开始 |
| Phase 3 | 飞书 Bot 接入（Webhook + 指令解析） | ✅ |
| Phase 4 | 择时 Agent + 持仓建议 + 每日复盘 | ✅ |
| Phase 5 | 风控自适应 + 策略画像 + 交易导入 | |
| Phase 6 | 实时异动监控 + 回测框架 | |

AI-native quantitative trading assistant. Multi-agent architecture for stock
selection, timing signals, position management, anomaly detection, and daily
review — delivered through Feishu.

## Architecture

```
Feishu / WeChat (notify + command trigger)
       |
  VPS (2C4G) — online layer
  |-- Feishu Bot (webhook + command parsing)
  |-- Redis Pub/Sub (message bus, task queue)
  |-- APScheduler (cron: daily review, open/close routines)
  |-- Nginx (reverse proxy)
       |  Tailscale/WireGuard internal network
  Mac Mini M4 — compute layer
  |-- Orchestrator (agent pipeline DAG)
  |-- Agents: StockSelector, TimingAgent, PositionAdvisor,
  |           AnomalyMonitor, DailyReviewer
  |-- Risk Engine (auto-adaptive level 1-5, manual override via Feishu)
  |-- Data Layer: AkShare (A-share pricing, fundamentals, news) +
  |               ChromaDB (historical pattern similarity)
  |-- LLM: DeepSeek (chat + reasoner)
  |-- Strategy Profiles (YAML-based trading bias templates)
```

## Features

| Feature | Agent | Trigger |
|---|---|---|
| Stock Selection | `StockSelector` | Pre-market cron or Feishu command |
| Position Advice | `PositionAdvisor` | On-demand or pre-market |
| Buy/Sell Timing | `TimingAgent` | Intraday signal + LLM context check |
| Anomaly Alerts | `AnomalyMonitor` | Real-time WebSocket push |
| Daily Review | `DailyReviewer` | Post-market cron |
| Risk Auto-Tuning | `RiskEngine` | Daily open-check scan |
| Strategy Profiles | profiles/*.yaml | Manual switch or auto-match |
| Trade Import | (Phase 2) | CSV parse + bias extraction |

### Feishu Commands (planned)

| Command | Action |
|---|---|
| `选股` / `选股 半导体` | Run stock selection, optional sector filter |
| `分析 600519` | Single-stock deep analysis |
| `持仓` | Portfolio diagnosis |
| `复盘` | Trigger daily review |
| `风险 3` | Lock risk level manually |

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12+ | |
| LLM | DeepSeek (chat + reasoner) | chat for screening, reasoner for analysis |
| Agent Framework | Self-built lightweight | No CrewAI/AutoGen dependency |
| Market Data | AkShare (free) | Tushare Pro optional |
| Vector DB | ChromaDB | Historical pattern retrieval |
| Message Queue | Redis Pub/Sub | Agent decoupling + VPS-Mac bridge |
| Scheduler | APScheduler | Cron jobs |
| Storage | SQLite + Parquet | PostgreSQL when scale demands |
| Notify | Feishu Webhook / Bot API | Lark Open API |
| Backtest | Backtrader (optional) | |

## Project Structure

```
effective-fishstick/
├── config/                 # Settings: YAML + Pydantic models
│   ├── settings.py         #   LLMConfig, DataConfig, RiskConfig, etc.
│   └── settings.yaml       #   Default values (override via env or .local.yaml)
├── data/                   # Data layer
│   ├── market.py           #   AkShare: daily/minute kline, indices
│   ├── fundamental.py      #   AkShare: PE, PB, ROE, revenue growth
│   └── news.py             #   AkShare: announcements, EastMoney news
├── agents/                 # Multi-agent layer
│   ├── base.py             #   BaseAgent, AgentContext, AgentResult
│   ├── orchestrator.py     #   Pipeline DAG scheduler
│   ├── stock_selector.py   #   Multi-factor screening + LLM analysis
│   ├── position_advisor.py #   Portfolio rebalancing recommendations
│   ├── timing.py           #   Technical signal + LLM context validation
│   ├── anomaly.py          #   Rule engine + LLM severity grading
│   └── reviewer.py         #   Post-market structured review report
├── engine/                 # Execution layer
│   └── risk.py             #   RiskEngine: scoring, level mapping, position calc
├── notify/                 # Notification
│   └── feishu.py           #   FeishuNotifier: text, card, alert, command ingest
├── storage/                # Persistence
│   ├── models.py           #   ORM: positions, signals, review logs
│   └── vector_store.py     #   ChromaDB: market state snapshots
├── profiles/               # Strategy bias templates
│   ├── default.yaml        #   Balanced multi-factor
│   ├── buffett_value.yaml  #   Value investing bias
│   └── momentum.yaml       #   Momentum/trading bias
├── web/                    # Future: optional dashboard
├── tests/
└── main.py                 # Entry point
```

## Quick Start

```bash
# Create virtual environment
python3.12 -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -e ".[dev]"

# Set required env vars
export DEEPSEEK_API_KEY="sk-your-key"
export FEISHU_WEBHOOK="https://open.feishu.cn/..."  # optional for now

# Run a placeholder command
python main.py
```

## Design Decisions

### Why not CrewAI / AutoGen / Hermes?

The five agents form a simple DAG (select -> time -> size -> risk-check, review
is independent), not a mesh. A lightweight Orchestrator (~150 lines) with
structured data passing between agents is more debuggable and avoids framework
lock-in. Heavy agent frameworks add value only when agent interaction patterns
become non-linear — revisit the decision then.

### DeepSeek model strategy

- `deepseek-chat` — fast, cheap — used for quantitative screening, news
  summarization, and routine tasks.
- `deepseek-reasoner` — strong reasoning — used for complex analysis: contextual
  signal validation, daily review synthesis, black-swan interpretation.

### Hardware split: VPS + Mac Mini

The VPS (2C4G) handles the always-on face: Feishu webhook, Redis, lightweight
scheduling. The Mac Mini M4 handles the heavy lifting: LLM inference, backtesting,
batch data processing. Connected via Tailscale for simplicity (no open ports).

### Risk auto-adaptation

A composite score (trend * 0.35 + inverse-volatility * 0.25 + macro * 0.20 +
sentiment * 0.20) maps to five levels. Each level gates position caps and stop-loss
width. Overridable via Feishu `风险 N` command or manual config edit.

### Strategy profiles for trade import

Rather than importing raw trade logs, profiles abstract a trader's behavioral
bias (PE ceiling, holding period, sector preference, stop strategy). These bias
parameters are injected into agent prompts and risk calculations. Future work:
back-calculate bias from actual trade CSV to auto-generate profiles.

## Open Questions (to resolve during build)

1. DeepSeek model split: chat for screening, reasoner for analysis — confirm
   after testing cost/latency on real workloads.
2. A-share only, or HK/US stocks too? Affects data source selection.
3. Feishu: webhook-only bot (simple) or custom app (interactive cards)? Start
   with webhook, upgrade when command parsing is needed.
4. Trade execution: simulation-only for now, no live brokerage connection.
5. Trade import format: define a standard CSV schema for external trade logs.
6. Local model: evaluate Qwen2.5-7B on M4 for coarse screening to reduce API cost.

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Project skeleton, config system, data layer (AkShare) | **done** |
| 2 | StockSelector agent with multi-factor screening + LLM | next |
| 3 | Feishu Bot: webhook push + command ingest | |
| 4 | TimingAgent + PositionAdvisor + DailyReviewer | |
| 5 | RiskEngine auto-adaptation + strategy profiles | |
| 6 | Real-time anomaly monitoring + backtest framework | |
| Phase 5 | 风控自适应 + 策略画像 + 交易导入 | ✅ |
