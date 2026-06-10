# Binance Square Momentum Bot

基于 Binance Square 热门内容和 Binance Spot 24h 行情的单向追涨交易机器人，附带 React Web 控制台。

**核心原则：** 做多头动能策略。市场动能先过硬筛，广场热度只做加分。默认 dry-run，不会真实下单；dry-run 默认按合约模拟展示杠杆、保证金、名义仓位和收益率。

**权衡：** 这个项目偏向谨慎筛选和可解释性，而不是高频触发。Binance Square 不是稳定公开接口，网页结构变化可能影响帖子解析；实盘前应长期模拟观察。

## 项目结构

```
├── binance_square_momentum_bot.py   # 核心交易引擎
├── web_dashboard.py                 # 本地 Web 后端（HTTP API + 静态文件托管）
├── web/
│   ├── src/                         # React + TypeScript 前端源码
│   │   ├── App.tsx                  # 主界面组件
│   │   ├── api.ts                   # API 请求层
│   │   ├── format.ts                # 格式化工具
│   │   ├── types.ts                 # TypeScript 类型定义
│   │   ├── styles.css               # 全局样式
│   │   └── main.tsx                 # 入口
│   ├── index.html                   # HTML 模板
│   ├── vite.config.ts               # Vite 构建配置
│   └── tsconfig.json                # TypeScript 配置
├── package.json                     # Node.js 依赖和构建脚本
├── requirements.txt                 # Python 依赖
├── start_dashboard.bat              # 一键启动控制台
├── fix_playwright_browser.bat       # 修复 Playwright 浏览器
├── install_browser_mode.bat         # 安装浏览器抓取模式
└── .gitignore
```

## 项目定位

**社媒热度辅助判断，不替代行情强度。**

脚本完成三件事：

- 抓取 Binance Square 热门帖子，统计真实币种提及量。
- 读取 Binance Spot 24h 涨幅、波动、成交额，生成市场动能评分。
- 综合市场分和广场分，选择只做多的候选标的。

为了降低误判，规则会剔除：

- 稳定币和法币类资产，例如 `USDC`、`USDT`、`FDUSD`、`TUSD`、`DAI`。
- 常见英文词误判，例如 `THE`、`AT`、`PEOPLE`、`NOT`。
- 明显做空或看空语境，例如 `SHORT POSITION`、`selling pressure`、`drops below`。

## 安装运行

**先安装依赖，再从 dry-run 开始。**

### Python 依赖

````powershell
python -m pip install -r requirements.txt
```

### 前端构建

````powershell
npm ci
npm run build
```

### 配置环境变量

````powershell
$env:BINANCE_API_KEY="你的 API Key"
$env:BINANCE_API_SECRET="你的 API Secret"
$env:ORDER_QUOTE_USDT="50"
$env:MAX_OPEN_POSITIONS="1"
$env:LEVERAGE_MULTIPLIER="10"
$env:CONTRACT_MAX_MARGIN_LOSS_PCT="20"
$env:LIQUIDATION_STOP_BUFFER_PCT="2"
$env:MIN_PRICE_CHANGE_PERCENT="3"
$env:MIN_VOLATILITY_PERCENT="5"
$env:MIN_QUOTE_VOLUME_USDT="5000000"
$env:POLL_SECONDS="300"
$env:TELEGRAM_ENABLED="false"
$env:TELEGRAM_BOT_TOKEN=""
$env:TELEGRAM_CHAT_ID=""
```

### 运行一次模拟

````powershell
python .\binance_square_momentum_bot.py --once
```

### 确认策略和风控后再使用实盘

````powershell
python .\binance_square_momentum_bot.py --live
```

## Web 控制台

**页面只负责控制和展示，API Key 只从环境变量读取。**

前端使用 React + Vite + TypeScript，源码在 `web/src/`，构建产物由 `web_dashboard.py` 本地托管。

启动本地控制台：

````powershell
.\start_dashboard.bat
```

浏览器打开：

```t`	ext
http://127.0.0.1:8787/
```

常用按钮：

- `刷新信号`：只计算候选，不下单。
- `诊断广场`：检查 Binance Square 是否能抓到可解析帖子。
- `执行一次`：执行一个完整周期；dry-run 模式只记录模拟交易。
- `启动循环`：按轮询间隔持续执行。
- `清空模拟仓位`：只在 dry-run 模式下清空本地 `bot_state.json`。

设置页支持策略参数预设：

- `保守`：提高入场门槛、收紧日内限制，默认只允许 1 个仓位。
- `标准`：沿用当前默认参数，适合先做模拟观察。
- `激进`：降低入场门槛、放宽持仓和日内限制，默认最多 3 个仓位。

首页持仓区域会展示当前持仓的入场价、有效止损价、止盈价和现价价格线；多仓位时，状态卡会显示主仓位并标记额外仓位数量。

杠杆倍数默认 `10` 倍，可在网页“基础”设置里自由调整，或通过 `LEVERAGE_MULTIPLIER` 设置。dry-run 默认开启 `CONTRACT_SIMULATION_ENABLED=true`，按 `单笔保证金 × 杠杆倍数` 计算名义仓位、ROI 和预估强平价；实盘仍不会自动切换为合约下单。
合约模拟会优先按保证金风险收紧止损，默认 `CONTRACT_MAX_MARGIN_LOSS_PCT=20`、`LIQUIDATION_STOP_BUFFER_PCT=2`；例如 `10x` 下即使配置 `20%` 初始止损，也会收紧为约 `2%` 价格止损，避免止损价低于预估强平价。

通知页支持 Telegram 推送：

- `启用 Telegram 通知`：开启后，买入、平仓、风控跳过和循环异常会推送到 Telegram。
- `Bot Token`：从 `@BotFather` 获取；后端不会在状态接口回显 Token。
- `Chat ID`：目标个人或群组 ID。
- `测试通知`：发送一条测试消息，用于确认 Token 和 Chat ID 是否可用。

浏览器抓取模式需要 Playwright Chromium：

````powershell
.\fix_playwright_browser.bat
```

## 评分规则

**硬条件先过滤，综合分再排序。**

入选候选前必须同时满足：

- `涨幅 >= MIN_PRICE_CHANGE_PERCENT`
- `波动 >= MIN_VOLATILITY_PERCENT`
- `成交额 >= MIN_QUOTE_VOLUME_USDT`
- 现货可交易，且不是稳定币/法币类资产

综合评分：

```t`	ext
市场分 = 涨幅 * 10 + 波动 * 4 + 成交额加分
广场分 = 当前币提及数 / 最高提及数 * 180
综合分 = 市场分 + 广场分
```

如果广场没有有效做多提及，系统仍会按 24h 市场动能排序，不会漏掉涨幅榜强势币。

## 风控逻辑

**严格单向操作，不做空。**

- 入场只使用 `BUY MARKET`。
- 平仓只卖出现有现货余额。
- 初始止损：默认开仓价下跌 `4%` 后触发；dry-run 合约模拟会再按 `CONTRACT_MAX_MARGIN_LOSS_PCT / LEVERAGE_MULTIPLIER` 和 `100 / LEVERAGE_MULTIPLIER - LIQUIDATION_STOP_BUFFER_PCT` 取更安全的有效止损。
- 止盈：默认 `0`，表示关闭固定止盈，让保本和移动止损负责退出；可通过 `TAKE_PROFIT_PCT` 或网页设置调整。
- 保本止损：默认最高价达到开仓价上方 `3%` 后，将动态止损抬到开仓价上方 `0.2%`，可通过 `BREAKEVEN_TRIGGER_PCT` / `BREAKEVEN_OFFSET_PCT` 或网页设置调整。
- 移动止盈：默认最高价达到开仓价上方 `6%` 后启用，价格从最高价回撤 `3%` 时平仓，可通过 `TRAILING_START_PCT` / `TRAILING_STOP_PCT` 或网页设置调整；回撤设为 `0` 可关闭。
- 固定金额止损：默认值为单笔金额的 `20%`（单笔 `50 USDT` 时为 `10 USDT`），只在固定止损模式启用后触发。
- 默认不会在首个买入-卖出回合后自动切换固定止损；可在网页勾选或设置 `FIXED_STOP_AFTER_FIRST_ROUND_TRIP=true` 启用。
- 可设置权益阈值，达到指定账户权益后再启用固定金额止损；不确定时建议留空。
- 冷却时间：默认同一币种卖出后 `30` 分钟内不重新开仓，可通过 `COOLDOWN_MINUTES` 或网页设置调整；设为 `0` 可关闭。
- 每日开仓上限：默认每天最多开仓 `5` 次，可通过 `MAX_DAILY_TRADES` 或网页设置调整；设为 `0` 可关闭。
- 每日亏损上限：默认当天已实现亏损达到 `25 USDT` 后停止新开仓，可通过 `MAX_DAILY_LOSS_USDT` 或网页设置调整；设为 `0` 可关闭。
- 绩效统计：网页交易记录页会基于已完成的买入-卖出回合统计胜率、总盈亏、平均盈亏、盈亏比、最大回撤和当前连胜/连亏；未平仓浮盈浮亏不计入已实现绩效。
- 多仓位管理：默认 `MAX_OPEN_POSITIONS=1`，可在网页设置最大持仓数；系统会跳过已持有币种，直到持仓数量低于上限才继续扫描新开仓。
- 订单安全检查：买入/卖出前会检查 Binance 交易规则中的最小数量、步进精度和最小成交额，避免明显不满足规则的实盘订单被拒绝。
- dry-run 成本估算：默认按 `0.1%` 手续费和 `0.05%` 滑点估算，买入价格上浮、卖出价格下调，绩效统计使用扣费后的净额；可通过 `FEE_RATE_PCT` / `SLIPPAGE_PCT` 或网页设置调整。
- 手动平仓：网页提供手动平仓按钮，模拟模式记录 `DRY_RUN_MANUAL_SELL`，实盘模式会二次确认后市价卖出现有仓位。
- 白名单/黑名单：可通过 `ASSET_WHITELIST` / `ASSET_BLACKLIST` 或网页设置控制允许交易的币种，支持 `BTC,ETH,SOL` 或 `SOLUSDT` 格式；白名单为空表示不限制。
- BTC/ETH 大盘过滤：默认关闭。启用 `MARKET_FILTER_ENABLED=true` 后，会检查 `MARKET_FILTER_ASSETS`（默认 `BTC,ETH`）的 24h 涨幅；若不满足 `MARKET_FILTER_MIN_CHANGE_PCT`（默认 `-1%`）则暂停新开仓。`MARKET_FILTER_REQUIRE_ALL=true` 可要求所有大盘币都满足。
- 成交后账户同步：默认开启 `ACCOUNT_SYNC_ENABLED=true`。实盘买入后会用账户余额校准本地持仓数量；卖出后如账户仍有剩余余额则保留剩余仓位，否则清空本地仓位；循环开始时也会校准已有实盘仓位。

## 技术栈

| 层级 | 技术 |
|------|------|
| 交易引擎 | Python 3.10+, requests, beautifulsoup4, playwright |
| Web 后端 | Python http.server（内置，无额外框架） |
| Web 前端 | React 19, TypeScript, Vite, lucide-react |

## GitHub 与安全

**提交代码，不提交账户状态和密钥。**

`.gitignore` 已忽略：

- `bot_state.json` / `bot_state.json.tmp`
- `dashboard.*.log`
- `__pycache__/` / `*.pyc`
- `node_modules/`
- `signal_records.jsonl`
- `web/dist/`

## 编码说明

**仓库文件统一按 UTF-8 维护。**

如果 Windows 终端里 README 中文显示乱码，通常是当前控制台编码不是 UTF-8，而不是文件损坏。可用下面方式确认：

````powershell
Get-Content .\README.md -Encoding UTF8
```

如需让当前 PowerShell 会话按 UTF-8 输出，可执行：

````powershell
[Console]::OutputEncoding = [System.T`	ext.Encoding]::UTF8
$OutputEncoding = [System.T`	ext.Encoding]::UTF8
```

---

**风险提示：** 自动交易可能造成真实亏损。本项目不是投资建议；任何实盘操作都应从小金额开始，并自行承担风险。

## 实盘前检查清单

在切换 `Live 实盘` 或使用 `python .\binance_square_momentum_bot.py --live` 前，请逐项确认：

- API key 只开启现货交易权限，不开启提现权限。
- API key 已在 Binance 后台开启 IP 白名单；Binance API 无法完整可靠读取该项，必须人工检查。
- 页面状态只显示 API key 是否加载和后 4 位，不会显示完整 key 或 secret。
- `run-once`、`start-loop`、`manual-close`、单仓位平仓在 live 模式下都需要二次确认；后端会拒绝没有 `live_confirmed=true` 的请求。
- 下单前会把 `pending_order` 写入 `bot_state.json`，异常后按 `clientOrderId` 查询订单状态，避免直接重复下单。
- live 买入成交后会尝试创建交易所端保护单：优先 OCO；当固定止盈为 `0` 时使用 stop-loss-limit 保护。保护单失败会写入日志、页面状态，并触发 Telegram 通知。
- dry-run 不会真实挂保护单，只会在状态中记录模拟保护单参数。
- 当前 futures/contract 显示仅是 dry-run 合约模拟；live 模式仍然只做 Binance Spot 现货交易，不会自动切换合约交易。

新增配置：

````powershell
$env:EXCHANGE_PROTECTION_ENABLED="true"
$env:OCO_STOP_LIMIT_SLIPPAGE_PCT="0.5"
```

## P1 信号可靠性过滤

自动入场前会额外做三类确认，用于减少 Square 数据失效或短线冲高回落时的误入场：

- `Square 置信度`：基于帖子数量、结构化信息、时间信息、抓取模式和连续失败次数评分；低于阈值时跳过自动交易。
- `短周期 K 线确认`：检查 5m / 15m / 1h ROC、5m EMA9 和高 24h 涨幅下的短线回落风险。
- `盘口流动性过滤`：检查 bid-ask spread 和买入侧 order book 深度，流动性不足时跳过候选币。

新增配置：

````powershell
$env:KLINE_CONFIRMATION_ENABLED="true"
$env:MIN_SQUARE_CONFIDENCE_SCORE="35"
$env:MAX_SPREAD_BPS="50"
$env:MIN_ORDERBOOK_DEPTH_USDT="1000"
```

当 Binance Square 没有有效做多提及时，自动入场不会再退化成纯 24h 市场动能追涨；页面首页会显示 `Entry confirmation` 的通过/阻塞原因。

## P2 账户级风控

账户级风控用于限制整体风险，而不是替代单笔止损。默认阈值为 `0`，表示关闭对应限制；设置后会在自动入场前检查：

- 总敞口：所有持仓加本次拟开仓金额占权益估算的比例。
- 单币敞口：当前候选币持仓加本次拟开仓金额占权益估算的比例。
- 连亏熔断：最近已完成交易连续亏损达到阈值后暂停新开仓。
- 日内回撤熔断：当日已实现亏损加当前浮亏达到阈值后暂停新开仓。
- 风险定仓建议：只计算建议下单金额，不改变当前固定 `ORDER_QUOTE_USDT` 下单逻辑。

新增配置：

````powershell
$env:MAX_TOTAL_EXPOSURE_PCT="0"
$env:MAX_SYMBOL_EXPOSURE_PCT="0"
$env:MAX_CONSECUTIVE_LOSSES="0"
$env:MAX_INTRADAY_DRAWDOWN_PCT="0"
$env:RISK_PER_TRADE_PCT="0"
```

页面首页会显示 `Account risk` 状态；设置页“风控退出”可以调整这些参数。

## Docker ??

????????

```powershell
docker compose up -d --build
```

??????

```text
http://127.0.0.1:8787/
```

??? Docker Hub?? `yourname/binance-momentum-dashboard:latest` ????

```powershell
docker build -t yourname/binance-momentum-dashboard:latest .
docker login
docker push yourname/binance-momentum-dashboard:latest
```

????? API Key ????????????????? `.env` ???

## P3 Engineering Validation Baseline

Use the following commands before committing local safety or dashboard changes:

````powershell
python -m py_compile .\binance_square_momentum_bot.py .\web_dashboard.py .\tools\analyze_signal_records.py .\tools\replay_signal_records.py .\tools\walk_forward_signal_records.py
python .\tests\test_safety_and_risk.py
npm ci
npm run build
```

`package-lock.json` is intentionally committed so `npm ci` can reproduce frontend installs. The dashboard still defaults to `127.0.0.1`; if `DASHBOARD_AUTH_TOKEN` is set, trading-control POST requests must include the same token from the browser settings page. Public deployment still requires `	external HTTPS, firewall rules, and IP allowlisting.

````powershell
$env:DASHBOARD_AUTH_TOKEN="your-local-token"
```

## P4 Signal Recording And Offline Dataset

- `SIGNAL_RECORDING_ENABLED=true` enables JSONL recording by default.
- `SIGNAL_RECORD_FILE=signal_records.jsonl` controls the local output file.
- Web `preview` and `run-once` write one record with Square confidence, post summaries, candidate scores, entry confirmation, K-line/orderbook checks, account risk, and final decision.
- Records are redacted and must not contain API keys, API secrets, Telegram tokens, or full account balance details.
- `signal_records.jsonl` is ignored by Git.
- Future-return updates only read market data and do not modify `bot_state.json`:

````powershell
python .\binance_square_momentum_bot.py --update-signal-returns
```

Analyze the local signal dataset:

````powershell
python .\tools\analyze_signal_records.py .\signal_records.jsonl
python .\tools\analyze_signal_records.py .\signal_records.jsonl --csv --output .\signal_records.csv
```

## P5 Offline Validation And Replay Draft

P5 adds lightweight validation on top of P4 signal records. It is not a full backtest engine and does not tune parameters automatically.

- `tools/analyze_signal_records.py` groups records by decision reason: entered, low Square confidence, K-line rejection, orderbook rejection, account-risk rejection, and other skips.
- The analyzer compares future returns for entered versus skipped records at 5m / 15m / 1h / 4h and reports mean, median, and positive-rate values.
- `tools/replay_signal_records.py` replays recorded decisions from JSONL and reports trade count, win rate, average return, max consecutive losses, and missed-upside / avoided-downside counts by decision group.
- Replay is read-only: it does not call Binance, does not modify `bot_state.json`, and does not place or simulate new orders outside the recorded dataset.

````powershell
python .\tools\replay_signal_records.py .\signal_records.jsonl --horizon 1h
```

## P9 Walk-Forward Validation Draft

P9 adds a lightweight walk-forward summary for existing signal records. It is offline-only: it does not call Binance, does not change bot state, does not tune parameters, and does not alter live trading behavior.

The default split is:

- Train: 60%
- Validation: 20%
- Test: 20%

Run it with:

````powershell
python .\tools\walk_forward_signal_records.py .\signal_records.jsonl
python .\tools\walk_forward_signal_records.py .\signal_records.jsonl --split 60,20,20
```

Each phase reports record count, entered/skipped count, decision groups, future-return summary for all records, and future-return summary for entered records only. Use this as an early guard against judging the strategy from one market segment.

## P6 CI And Dependency Stability

This repository includes a lightweight GitHub Actions baseline in `.github/workflows/ci.yml`.
It runs the same validation commands recommended for local pre-commit checks:

````powershell
python -m py_compile .\binance_square_momentum_bot.py .\web_dashboard.py .\tools\analyze_signal_records.py .\tools\replay_signal_records.py .\tools\walk_forward_signal_records.py
python .\tests\test_safety_and_risk.py
npm ci
npm run build
```

CI does not require Binance or Telegram credentials, does not call live trading endpoints, and does not run the bot in live mode.

Dependency defaults:

- Frontend installs should use `npm ci`; `package-lock.json` is intentionally committed.
- Python dependencies remain in `requirements.txt` for now.
- No `uv.lock`, `requirements.lock`, or pip-tools migration is included in this phase.

Additional test coverage now includes symbol `	extraction, Square mention counting, score ordering, Decimal rounding, Binance filter parsing, state migration, signal JSONL analysis, replay summaries, and dry-run fill behavior.

## Remote Dashboard Safety

The web dashboard is designed for local use first. It binds to `127.0.0.1` by default, and `DASHBOARD_AUTH_TOKEN` is only a local control-layer token.

If you deploy the dashboard on a VPS or expose it beyond localhost, add `	external protection before enabling trading controls:

- HTTPS reverse proxy.
- Firewall and IP allowlist.
- `DASHBOARD_AUTH_TOKEN`.
- Binance API key with no withdrawal permission.
- Binance API IP whitelist.
- Live-mode confirmation for every trading control request.

Do not treat the dashboard token alone as a complete public-internet authentication system.

## P7 Dashboard Read-Only Safety Mode

Use `DASHBOARD_READ_ONLY=true` when the dashboard should be observable but must not run control actions.

````powershell
$env:DASHBOARD_READ_ONLY="true"
python .\web_dashboard.py
```

Read-only mode blocks POST control routes including preview, Square diagnostics, future-return updates, run-once, start-loop, stop, manual close, position close, dry-run state reset, and Telegram test sends. It keeps read-only GET routes available, including `/api/status`, `/api/market-chart`, and static frontend assets.

`/api/status` exposes a `dashboard_security` snapshot with:

- read-only enabled / disabled.
- dashboard token enabled / disabled.
- Host / Origin checking status.
- bound host and whether it is local-only.

The frontend displays this dashboard security state and disables control buttons when read-only mode is enabled. This is an application-level safeguard only; VPS or public deployments still need HTTPS reverse proxy, firewall, IP allowlist, Binance API IP whitelist, and no-withdrawal API keys.

## P8 Dependabot Dependency Alerts

GitHub Dependabot is configured in `.github/dependabot.yml` to check dependencies weekly:

- `npm` for frontend dependencies in `package.json` / `package-lock.json`.
- `pip` for Python dependencies in `requirements.txt`.

Dependabot only opens update PRs. It does not auto-merge, does not run the bot, does not read API keys, and does not change live trading behavior. Review each Dependabot PR manually and require the normal validation baseline before merging:

````powershell
python -m py_compile .\binance_square_momentum_bot.py .\web_dashboard.py .\tools\analyze_signal_records.py .\tools\replay_signal_records.py .\tools\walk_forward_signal_records.py
python .\tests\test_safety_and_risk.py
npm ci
npm run build
```

The dependency strategy stays conservative in this phase: frontend installs use `npm ci`, Python stays on `requirements.txt`, and no `uv.lock`, `requirements.lock`, pip-tools, or automatic dependency update merge policy is introduced.
