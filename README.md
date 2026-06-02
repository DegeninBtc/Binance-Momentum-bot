# Binance Square Momentum Bot

基于 Binance Square 热门内容和 Binance Spot 24h 行情的单向追涨交易控制台。

**核心原则：** 只做现货多头。市场动能先过硬筛，广场热度只做加分。默认 dry-run，不会真实下单。

**权衡：** 这个项目偏向谨慎筛选和可解释性，而不是高频触发。Binance Square 不是稳定公开接口，网页结构变化可能影响帖子解析；实盘前应长期模拟观察。

## 1. 项目定位

**社媒热度辅助判断，不替代行情强度。**

脚本会完成三件事：

- 抓取 Binance Square 热门帖子，统计真实币种提及量。
- 读取 Binance Spot 24h 涨幅、波动、成交额，生成市场动能评分。
- 综合市场分和广场分，选择只做多的候选标的。

为了降低误判，规则会剔除：

- 稳定币和法币类资产，例如 `USDC`、`USDT`、`FDUSD`、`TUSD`、`DAI`。
- 常见英文词误判，例如 `THE`、`AT`、`PEOPLE`、`NOT`。
- 明显做空或看空语境，例如 `SHORT POSITION`、`selling pressure`、`drops below`。

## 2. 安装运行

**先安装依赖，再从 dry-run 开始。**

```powershell
python -m pip install -r requirements.txt
```

配置环境变量：

```powershell
$env:BINANCE_API_KEY="你的 API Key"
$env:BINANCE_API_SECRET="你的 API Secret"
$env:ORDER_QUOTE_USDT="50"
$env:MIN_PRICE_CHANGE_PERCENT="3"
$env:MIN_VOLATILITY_PERCENT="5"
$env:MIN_QUOTE_VOLUME_USDT="5000000"
$env:POLL_SECONDS="300"
```

运行一次模拟：

```powershell
python .\binance_square_momentum_bot.py --once
```

确认策略和风控后再使用实盘：

```powershell
python .\binance_square_momentum_bot.py --live
```

## 3. Web 控制台

**页面只负责控制和展示，API Key 只从环境变量读取。**

启动本地控制台：

```powershell
.\start_dashboard.bat
```

浏览器打开：

```text
http://127.0.0.1:8787/
```

常用按钮：

- `刷新信号`：只计算候选，不下单。
- `诊断广场`：检查 Binance Square 是否能抓到可解析帖子。
- `执行一次`：执行一个完整周期；dry-run 模式只记录模拟交易。
- `启动循环`：按轮询间隔持续执行。
- `清空模拟仓位`：只在 dry-run 模式下清空本地 `bot_state.json`。

浏览器抓取模式需要 Playwright Chromium：

```powershell
.\fix_playwright_browser.bat
```

## 4. 评分规则

**硬条件先过滤，综合分再排序。**

入选候选前必须同时满足：

- `涨幅 >= MIN_PRICE_CHANGE_PERCENT`
- `波动 >= MIN_VOLATILITY_PERCENT`
- `成交额 >= MIN_QUOTE_VOLUME_USDT`
- 现货可交易，且不是稳定币/法币类资产

综合评分：

```text
市场分 = 涨幅 * 10 + 波动 * 4 + 成交额加分
广场分 = 当前币提及数 / 最高提及数 * 180
综合分 = 市场分 + 广场分
```

如果广场没有有效做多提及，系统仍会按 24h 市场动能排序，不会漏掉涨幅榜强势币。

## 5. 风控逻辑

**严格单向操作，不做空。**

- 入场只使用 `BUY MARKET`。
- 平仓只卖出现有现货余额。
- 初始止损：默认开仓价下跌 `20%` 后触发。
- 固定金额止损：完成首个买入-卖出回合后，单仓浮亏达到 `200 USDT` 触发。
- 可设置权益阈值，达到指定账户权益后再启用固定金额止损。

## 6. GitHub 与安全

**提交代码，不提交账户状态和密钥。**

`.gitignore` 已忽略：

- `bot_state.json`
- `bot_state.json.tmp`
- `dashboard.*.log`
- `__pycache__/`
- `*.pyc`

使用 SSH 推送到 GitHub：

```powershell
git remote add origin git@github.com:你的用户名/你的仓库.git
git add .
git commit -m "Initial Binance momentum dashboard"
git push -u origin master
```

如果 SSH 未配置，请先在 GitHub 添加本机公钥，再测试：

```powershell
ssh -T git@github.com
```

---

**风险提示：** 自动交易可能造成真实亏损。本项目不是投资建议；任何实盘操作都应从小金额开始，并自行承担风险。
