# Binance Square Momentum Bot

一个基于 Binance Square 热度、Binance 行情动能和多层风控的多头动能交易机器人，带本地 React Dashboard。

默认运行在 **dry-run 模拟模式**，不会真实下单。当前默认市场模式是 **USDT-M 合约优先，Spot 现货兜底**：

- 有 USDT-M 合约的标的优先按合约行情和合约交易路径处理。
- 没有合约但有现货的标的可作为 fallback。
- live 模式需要二次确认；API key 只从环境变量读取，不从网页输入。

> 风险提示：自动交易可能造成真实亏损。本项目不是投资建议。实盘前请长期 dry-run 验证，并从小金额开始。

## 功能概览

- Binance Square 热门帖子抓取与币种提及统计。
- USDT-M Futures 优先、Spot fallback 的 24h 动能筛选。
- Square 置信度、5m / 15m / 1h K 线确认、盘口流动性过滤。
- 多仓位管理、冷却时间、每日开仓限制、每日亏损限制。
- 账户级风控：总敞口、单币敞口、连亏熔断、日内回撤熔断。
- 合约模拟强平保护止损：避免止损价低于或贴近预估强平价。
- live 交易幂等保护：`clientOrderId`、pending order、异常后查单恢复。
- 本地 Web Dashboard：信号、持仓、交易记录、风控、安全状态、日志、通知、设置。
- Telegram 通知：买入、平仓、风控跳过、循环异常。
- 信号 JSONL 记录、SQLite 交易复盘库、离线分析和导出工具。
- GitHub Actions CI 与 Dependabot 依赖提醒。

## 项目结构

```text
.
├── binance_square_momentum_bot.py   # 核心交易引擎
├── web_dashboard.py                 # 本地 HTTP API + Dashboard 静态托管
├── web/                             # React + TypeScript 前端
├── tests/                           # 轻量安全/风控/复盘测试
├── tools/                           # 信号与交易复盘分析工具
├── .github/                         # CI 和 Dependabot
├── Dockerfile
├── docker-compose.yml
├── package.json
├── package-lock.json
└── requirements.txt
```

## 快速开始

### 1. 安装依赖

```powershell
python -m pip install -r requirements.txt
npm ci
npm run build
```

如需浏览器模式抓取 Binance Square：

```powershell
.\fix_playwright_browser.bat
```

### 2. 配置环境变量

最小 dry-run 配置：

```powershell
$env:ORDER_QUOTE_USDT="50"
$env:MAX_OPEN_POSITIONS="15"
$env:LEVERAGE_MULTIPLIER="3"
$env:TRADE_MARKET_MODE="futures_preferred"
$env:FUTURES_MARGIN_TYPE="ISOLATED"
$env:POLL_SECONDS="300"
```

live 模式还需要：

```powershell
$env:BINANCE_API_KEY="你的 API Key"
$env:BINANCE_API_SECRET="你的 API Secret"
```

Telegram 可选：

```powershell
$env:TELEGRAM_ENABLED="true"
$env:TELEGRAM_BOT_TOKEN="你的 Telegram Bot Token"
$env:TELEGRAM_CHAT_ID="你的 Chat ID"
```

### 3. 启动 Dashboard

```powershell
.\start_dashboard.bat
```

浏览器打开：

```text
http://127.0.0.1:8787/
```

### 4. 命令行运行

运行一次 dry-run：

```powershell
python .\binance_square_momentum_bot.py --once
```

循环 dry-run：

```powershell
python .\binance_square_momentum_bot.py
```

live 模式：

```powershell
python .\binance_square_momentum_bot.py --live
```

## Dashboard 使用说明

常用按钮：

- `刷新信号`：只做手动预览，不下单。
- `诊断广场`：检查 Binance Square 抓取和解析状态。
- `执行一次`：执行完整策略周期；dry-run 只记录模拟交易。
- `启动循环`：按 `POLL_SECONDS` 自动扫描、管理持仓、复核策略并执行。
- `手动平仓`：平掉当前持仓；live 模式需要二次确认。
- `清空模拟仓位`：只清空本地 dry-run 仓位，不清空复盘数据库。

策略预设：

| 预设 | 用途 | 杠杆 | 最大持仓 |
| --- | --- | ---: | ---: |
| 保守 | 更高门槛、更低频 | 当前设置 | 1 |
| 标准 | 默认观察模式 | 3x | 15 |
| 激进 | 更宽松、更高风险 | 5x | 20 |

## 核心策略

入场候选必须先通过硬过滤：

- 24h 涨幅达到 `MIN_PRICE_CHANGE_PERCENT`。
- 波动率达到 `MIN_VOLATILITY_PERCENT`。
- 成交额达到 `MIN_QUOTE_VOLUME_USDT`。
- 标的不是稳定币、法币类资产或常见误识别英文词。
- 市场模式允许该标的：USDT-M 合约优先，Spot fallback。

综合评分：

```text
市场分 = 涨幅 * 10 + 波动 * 4 + 成交额加分
广场分 = 当前币提及数 / 最高提及数 * 180
综合分 = 市场分 + 广场分
```

入场前还会检查：

- Square 数据置信度。
- 5m / 15m / 1h 短周期 K 线确认。
- order book spread 和深度。
- 账户级风控和每日限制。

## 市场模式

`TRADE_MARKET_MODE` 支持：

| 值 | 行为 |
| --- | --- |
| `futures_preferred` | 默认。USDT-M 合约优先，Spot 兜底 |
| `futures_only` | 只扫描和交易 USDT-M 合约 |
| `spot_only` | 只扫描和交易 Spot 现货 |

合约设置：

```powershell
$env:FUTURES_BASE_URL="https://fapi.binance.com"
$env:FUTURES_MARGIN_TYPE="ISOLATED"
$env:LEVERAGE_MULTIPLIER="3"
```

live 合约开仓前会尝试设置逐仓/全仓模式和杠杆；合约平仓使用 reduce-only 市价单。

## 风控与安全

### 单笔退出

- 初始止损：`INITIAL_STOP_LOSS_PCT`，默认 `4%`。
- 固定止盈：`TAKE_PROFIT_PCT`，默认 `0` 表示关闭。
- 保本止损：`BREAKEVEN_TRIGGER_PCT` / `BREAKEVEN_OFFSET_PCT`。
- 移动止损：`TRAILING_START_PCT` / `TRAILING_STOP_PCT`。
- 固定金额止损：`FIXED_STOP_LOSS_USDT`。

合约模拟和合约 live 会额外使用强平保护：

```text
有效价格止损比例 = min(
  INITIAL_STOP_LOSS_PCT,
  CONTRACT_MAX_MARGIN_LOSS_PCT / LEVERAGE_MULTIPLIER,
  100 / LEVERAGE_MULTIPLIER - LIQUIDATION_STOP_BUFFER_PCT
)
```

默认：

```powershell
$env:CONTRACT_MAX_MARGIN_LOSS_PCT="20"
$env:LIQUIDATION_STOP_BUFFER_PCT="2"
```

### 账户级风控

```powershell
$env:MAX_TOTAL_EXPOSURE_PCT="0"
$env:MAX_SYMBOL_EXPOSURE_PCT="0"
$env:MAX_CONSECUTIVE_LOSSES="0"
$env:MAX_INTRADAY_DRAWDOWN_PCT="0"
$env:RISK_PER_TRADE_PCT="0"
```

默认 `0` 表示关闭对应限制。

### live 安全检查

live 前请确认：

- API key 禁止提现权限。
- API key 开启 IP 白名单。
- 合约优先模式需要 Futures 权限；Spot fallback 需要 Spot 权限。
- Dashboard 只显示 API key 是否加载和后 4 位，不显示完整 key/secret。
- `run-once`、`start-loop`、`manual-close`、单仓位平仓都需要 `live_confirmed=true`。
- 合约保护单第一版不挂交易所端条件单，使用本地轮询止盈/止损后 reduce-only 平仓。

## Dashboard 安全

默认只绑定本机：

```text
127.0.0.1:8787
```

可设置本地控制 token：

```powershell
$env:DASHBOARD_AUTH_TOKEN="your-local-token"
```

观察模式：

```powershell
$env:DASHBOARD_READ_ONLY="true"
python .\web_dashboard.py
```

如果部署到 VPS 或公网，必须额外使用 HTTPS 反代、防火墙、IP 白名单和 Binance API IP 白名单。不要把 `DASHBOARD_AUTH_TOKEN` 当成完整公网鉴权方案。

## 数据记录与复盘

本项目默认不提交本地状态和交易数据。

| 文件 | 用途 | 是否提交 |
| --- | --- | --- |
| `bot_state.json` | 当前状态、仓位、pending order | 否 |
| `signal_records.jsonl` | 信号和决策记录 | 否 |
| `trade_journal.sqlite3` | 长期交易复盘库 | 否 |
| `dashboard.*.log` | 本地运行日志 | 否 |

信号记录：

```powershell
$env:SIGNAL_RECORDING_ENABLED="true"
$env:SIGNAL_RECORD_FILE="signal_records.jsonl"
```

更新未来收益观察字段：

```powershell
python .\binance_square_momentum_bot.py --update-signal-returns
```

分析信号：

```powershell
python .\tools\analyze_signal_records.py .\signal_records.jsonl
python .\tools\replay_signal_records.py .\signal_records.jsonl --horizon 1h
python .\tools\walk_forward_signal_records.py .\signal_records.jsonl
```

分析交易复盘库：

```powershell
python .\tools\analyze_trade_journal.py .\trade_journal.sqlite3
python .\tools\export_trade_journal.py .\trade_journal.sqlite3 --view round_trips --output .\trade_journal_round_trips.csv
python .\tools\export_trade_journal.py .\trade_journal.sqlite3 --view events --output .\trade_journal_events.csv
```

## Docker

启动：

```powershell
docker compose up -d --build
```

打开：

```text
http://127.0.0.1:8787/
```

不要把 API key 写进镜像。使用环境变量或 `.env`，并确保 `.env` 不提交到 GitHub。

## 验收命令

提交前建议运行：

```powershell
python -m py_compile .\binance_square_momentum_bot.py .\web_dashboard.py .\tools\analyze_signal_records.py .\tools\replay_signal_records.py .\tools\walk_forward_signal_records.py .\tools\analyze_trade_journal.py .\tools\export_trade_journal.py
python .\tests\test_safety_and_risk.py
npm ci
npm run build
```

CI 会运行同类检查，不需要 Binance 或 Telegram 密钥，不会启动 live 交易。

## 依赖更新

Dependabot 已配置：

- `npm`：检查 `package.json` / `package-lock.json`。
- `pip`：检查 `requirements.txt`。

Dependabot 只创建 PR，不自动合并。合并前请先跑验收命令。

## 编码说明

仓库文本文件按 UTF-8 维护。Windows PowerShell 如显示中文乱码，可执行：

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
```

## License

未声明许可证。使用、分发或商用前请先确认仓库所有者授权。
