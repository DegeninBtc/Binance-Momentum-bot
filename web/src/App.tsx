import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  AlertCircle,
  BarChart3,
  BookOpen,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Database,
  Flame,
  Home,
  KeyRound,
  Moon,
  Play,
  Power,
  RefreshCw,
  Search,
  Settings as SettingsIcon,
  Shield,
  Square,
  Star,
  Sun,
  Target,
  Trash2,
  TrendingDown,
  TrendingUp,
  Wallet,
} from "lucide-react";
import { fetchStatus, postAction } from "./api";
import {
  actionLabel,
  asNumber,
  formatMoney,
  formatPercent,
  formatPrice,
  formatQty,
  formatScore,
  formatTime,
  signedMoney,
  signedPercent,
  stopModeLabel,
  textValue,
  tradeAmount,
  trimNumber,
} from "./format";
import type {
  ConfigPayload,
  DashboardStatus,
  Diagnostics,
  EntryGuardSnapshot,
  HotAsset,
  PerformanceStats,
  Position,
  PositionSnapshot,
  SettingsState,
  SettingsTabKey,
  TabKey,
  TradeItem,
} from "./types";

const DEFAULT_SETTINGS: SettingsState = {
  quote_asset: "USDT",
  order_quote_amount: "50",
  state_file: "bot_state.json",
  min_price_change_percent: "3",
  min_volatility_percent: "5",
  min_quote_volume: "5000000",
  top_post_limit: "25",
  top_coin_limit: "10",
  asset_whitelist: "",
  asset_blacklist: "",
  market_filter_assets: "BTC,ETH",
  market_filter_min_change_pct: "-1",
  initial_stop_loss_pct: "20",
  take_profit_pct: "12",
  breakeven_trigger_pct: "6",
  breakeven_offset_pct: "0",
  trailing_start_pct: "8",
  trailing_stop_pct: "5",
  fixed_stop_loss_usdt: "10",
  fixed_stop_equity_usdt: "",
  cooldown_minutes: "30",
  max_daily_trades: "5",
  max_daily_loss_usdt: "25",
  fee_rate_pct: "0.1",
  slippage_pct: "0.05",
  poll_seconds: "300",
  recv_window_ms: "5000",
  testnet: false,
  live: false,
  square_browser_mode: false,
  fixed_stop_after_first_round_trip: false,
  market_filter_enabled: false,
  market_filter_require_all: false,
  account_sync_enabled: true,
};

const TAB_ITEMS: Array<{ key: TabKey; label: string; icon: LucideIcon }> = [
  { key: "hot", label: "热门币种", icon: Flame },
  { key: "trades", label: "交易记录", icon: CircleDollarSign },
  { key: "diag", label: "广场诊断", icon: Search },
  { key: "logs", label: "日志", icon: Database },
  { key: "settings", label: "设置", icon: SettingsIcon },
];

const SETTINGS_TABS: Array<{ key: SettingsTabKey; label: string }> = [
  { key: "basic", label: "基础" },
  { key: "signal", label: "信号筛选" },
  { key: "scope", label: "交易范围" },
  { key: "risk", label: "风控退出" },
  { key: "cost", label: "交易成本" },
  { key: "runtime", label: "运行模式" },
];

function App() {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [settings, setSettings] = useState<SettingsState>(DEFAULT_SETTINGS);
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const [fixedStopEdited, setFixedStopEdited] = useState(false);
  const [activeTab, setActiveTab] = useState<TabKey>("hot");
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTabKey>("basic");
  const [busyPath, setBusyPath] = useState("");
  const [requestError, setRequestError] = useState("");
  const [theme, setTheme] = useState(() => localStorage.getItem("dashboard-theme") || "light");

  const refresh = useCallback(async () => {
    try {
      const data = await fetchStatus();
      setStatus(data);
      setRequestError("");
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Request failed");
    }
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("dashboard-theme", theme);
  }, [theme]);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 2500);
    return () => window.clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!settingsHydrated && status?.config) {
      setSettings(settingsFromConfig(status.config));
      setSettingsHydrated(true);
    }
  }, [settingsHydrated, status?.config]);

  const config = status?.config || {};
  const state = status?.state || {};
  const signal = status?.last_signal || {};
  const candidate = signal.candidate || null;
  const position = state.position || null;
  const snapshot = state.position_snapshot || null;
  const guard = state.entry_guard_snapshot || null;
  const performance = state.performance_stats || null;
  const trades = state.trade_log || [];
  const diagnostics = status?.last_diagnostics || null;
  const hasError = Boolean(requestError || status?.last_error);
  const running = Boolean(status?.running);
  const keysLoaded = Boolean(config.api_key_loaded && config.api_secret_loaded);
  const liveMode = settings.live || config.dry_run === false;
  const quoteAsset = snapshot?.quote_asset || textValue(config.quote_asset) || settings.quote_asset || "USDT";
  const updatedAt = status?.last_finished_at || status?.last_started_at || "--";

  const riskTone = useMemo(() => {
    if (hasError || snapshot?.stop_triggered || guard?.entry_blocked) {
      return "danger";
    }
    if (snapshot?.take_profit_triggered) {
      return "success";
    }
    if (running || liveMode || !keysLoaded) {
      return "warning";
    }
    return "success";
  }, [guard?.entry_blocked, hasError, keysLoaded, liveMode, running, snapshot?.stop_triggered, snapshot?.take_profit_triggered]);

  function updateSetting<K extends keyof SettingsState>(key: K, value: SettingsState[K]) {
    setSettings((current) => {
      const next = { ...current, [key]: value };
      if (key === "order_quote_amount" && !fixedStopEdited) {
        const amount = Number(value);
        if (Number.isFinite(amount) && amount > 0) {
          next.fixed_stop_loss_usdt = formatDefaultFixedStop(amount);
        }
      }
      return next;
    });
  }

  async function submit(path: string, nextTab?: TabKey) {
    setBusyPath(path);
    if (nextTab) {
      setActiveTab(nextTab);
    }
    try {
      const data = await postAction(path, settings);
      setStatus(data);
      setRequestError("");
      window.setTimeout(refresh, 800);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Request failed");
    } finally {
      setBusyPath("");
    }
  }

  function manualClose() {
    const message = settings.live
      ? "确认实盘市价卖出当前仓位？这个操作会真实下单。"
      : "确认模拟卖出当前仓位？";
    if (window.confirm(message)) {
      submit("/api/manual-close");
    }
  }

  function resetState() {
    if (window.confirm("清空 bot_state.json 中的模拟仓位和交易记录？")) {
      submit("/api/reset-dry-run-state", "trades");
    }
  }

  return (
    <div className="app-shell">
      <aside className="side-rail" aria-label="主导航">
        <div className="rail-logo">B</div>
        <nav className="rail-nav">
          <button className="is-active" type="button" title="首页">
            <Home size={20} />
          </button>
          <button type="button" title="行情">
            <BarChart3 size={20} />
          </button>
          <button type="button" title="策略">
            <Activity size={20} />
          </button>
          <button type="button" title="记录">
            <Database size={20} />
          </button>
          <button type="button" title="说明">
            <BookOpen size={20} />
          </button>
          <button type="button" title="设置" onClick={() => setActiveTab("settings")}>
            <SettingsIcon size={20} />
          </button>
        </nav>
        <button className="rail-collapse" type="button" title="收起">
          <span>›</span>
        </button>
      </aside>

      <header className="topbar">
        <div className="brand-block">
          <div>
            <p className="eyebrow">Binance Square Momentum</p>
          </div>
        </div>
        <div className="top-status">
          <StatusBadge
            tone={hasError ? "danger" : running ? "warning" : "success"}
            icon={running ? Activity : hasError ? AlertCircle : CheckCircle2}
            label={running ? "Running" : hasError ? "Error" : "Idle"}
            active={running}
          />
          <StatusBadge
            tone={keysLoaded ? "success" : "warning"}
            icon={KeyRound}
            label={keysLoaded ? "Keys Ready" : "Keys Missing"}
          />
          <StatusBadge tone={liveMode ? "danger" : "muted"} icon={Shield} label={liveMode ? "Live 实盘" : "Dry-run 模拟"} />
          <button
            className="icon-button"
            type="button"
            title={theme === "dark" ? "切换亮色" : "切换暗色"}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
        </div>
      </header>

      <main className="dashboard">
        <section className="market-hero">
          <div className="hero-copy">
            <div className="hero-title-row">
              <div>
                <p><span>市场指令</span> <strong>Market Command</strong></p>
                <h1>{candidate?.symbol || position?.symbol || "OPNUSDT"}</h1>
              </div>
              <Star className="hero-star" size={28} />
            </div>
            <div className="hero-score-row">
              <HeroScore label="综合分" value={formatScore(candidate?.combined_score ?? candidate?.price_change_percent ?? 0)} />
              <HeroScore label="24h 涨幅" value={formatPercent(candidate?.price_change_percent ?? 0)} tone="positive" />
              <HeroScore label="波动率" value={formatPercent(candidate?.volatility_percent ?? 0)} tone="warning" />
            </div>
            <MarketCurve />
            <div className="range-tabs" aria-label="行情周期">
              <span>1H</span>
              <span>6H</span>
              <span className="is-active">24H</span>
              <span>7D</span>
              <span>30D</span>
            </div>
          </div>
          <div className="hero-meta">
            <Database className="source-watermark" size={42} />
            <span>数据源</span>
            <strong>{signal.source || "--"}</strong>
            <small>{signal.checked_at ? `检查于 ${signal.checked_at}` : signal.note || "等待首次刷新"}</small>
          </div>
        </section>

        <section className="overview-grid">
          <MetricCard
            label="当前仓位"
            value={positionLabel(position, snapshot)}
            detail={positionDetail(position, snapshot)}
            icon={Wallet}
          />
          <MetricCard
            label="浮动盈亏"
            value={snapshot?.market_value ? `${signedMoney(snapshot.unrealized_pnl, quoteAsset)} · ${signedPercent(snapshot.unrealized_pnl_pct)}` : "--"}
            detail={snapshot?.market_value ? `市值 ${formatMoney(snapshot.market_value, quoteAsset)} · 本金 ${formatMoney(snapshot.quote_spent, quoteAsset)}` : snapshot?.price_error || "等待当前价格"}
            icon={asNumber(snapshot?.unrealized_pnl) !== null && Number(snapshot?.unrealized_pnl) < 0 ? TrendingDown : TrendingUp}
            tone={asNumber(snapshot?.unrealized_pnl) !== null && Number(snapshot?.unrealized_pnl) < 0 ? "danger" : "success"}
          />
          <MetricCard
            label="运行 / 风控"
            value={status?.mode || "idle"}
            detail={riskSummary(snapshot, guard, state.completed_round_trips)}
            icon={Target}
            tone={riskTone}
          />
          <MetricCard
            label="最后更新"
            value={updatedAt}
            detail={requestError || status?.last_error || `轮询 ${settings.poll_seconds || "300"} 秒 · 页面 2.5 秒刷新`}
            icon={Clock3}
            tone={hasError ? "danger" : "muted"}
          />
        </section>

        <section className="command-panel">
          <div className="command-title">
            <p className="eyebrow">Actions</p>
            <h2>操作中枢</h2>
          </div>
          <div className="command-grid">
            <ActionButton icon={RefreshCw} label="刷新信号" busy={busyPath === "/api/preview"} onClick={() => submit("/api/preview", "hot")} />
            <ActionButton icon={Search} label="诊断广场" busy={busyPath === "/api/square-diagnose"} onClick={() => submit("/api/square-diagnose", "diag")} />
            <ActionButton icon={Play} label="执行一次" tone="primary" busy={busyPath === "/api/run-once"} onClick={() => submit("/api/run-once", "hot")} />
            <ActionButton icon={Activity} label="启动循环" busy={busyPath === "/api/start-loop"} onClick={() => submit("/api/start-loop")} />
            <ActionButton icon={Square} label="停止" tone="danger" busy={busyPath === "/api/stop"} onClick={() => submit("/api/stop")} />
            <ActionButton icon={Power} label="手动平仓" tone="danger" busy={busyPath === "/api/manual-close"} onClick={manualClose} />
            <ActionButton icon={Trash2} label="清空模拟仓位" tone="danger" busy={busyPath === "/api/reset-dry-run-state"} onClick={resetState} />
          </div>
        </section>

        <nav className="tabs" aria-label="Dashboard sections">
          <div className="tab-list">
            {TAB_ITEMS.map((item) => (
              <button
                key={item.key}
                className={`tab-button ${activeTab === item.key ? "is-active" : ""}`}
                type="button"
                onClick={() => setActiveTab(item.key)}
              >
                <item.icon size={16} />
                {item.label}
              </button>
            ))}
          </div>
          {activeTab === "hot" ? (
            <label className="coin-search">
              <Search size={16} />
              <input placeholder="搜索币种" />
            </label>
          ) : null}
        </nav>

        <section className="tab-surface">
          {activeTab === "hot" && <HotAssetsTable items={signal.hot_assets || []} />}
          {activeTab === "trades" && <TradesPanel stats={performance} trades={trades} />}
          {activeTab === "diag" && <DiagnosticsPanel diagnostics={diagnostics} />}
          {activeTab === "logs" && <LogsPanel logs={status?.logs || []} requestError={requestError} />}
          {activeTab === "settings" && (
            <SettingsPanel
              activeTab={activeSettingsTab}
              settings={settings}
              setActiveTab={setActiveSettingsTab}
              setFixedStopEdited={setFixedStopEdited}
              updateSetting={updateSetting}
            />
          )}
        </section>
      </main>
    </div>
  );
}

function StatusBadge({
  tone,
  icon: Icon,
  label,
  active = false,
}: {
  tone: "success" | "warning" | "danger" | "muted";
  icon: LucideIcon;
  label: string;
  active?: boolean;
}) {
  return (
    <span className={`status-badge tone-${tone} ${active ? "is-pulsing" : ""}`}>
      <Icon size={14} />
      {label}
    </span>
  );
}

function HeroScore({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "positive" | "warning" }) {
  return (
    <div className={`hero-score tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function MarketCurve() {
  return (
    <div className="market-curve" aria-hidden="true">
      <svg viewBox="0 0 760 150" preserveAspectRatio="none">
        <defs>
          <linearGradient id="curveFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.2" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path className="curve-fill" d="M0 122 L30 120 L58 106 L82 111 L110 98 L136 91 L165 73 L190 94 L214 86 L240 91 L268 84 L296 80 L326 72 L354 79 L382 66 L410 70 L438 63 L468 74 L492 59 L520 51 L548 57 L580 43 L608 50 L635 36 L664 47 L692 41 L724 44 L760 26 L760 150 L0 150 Z" />
        <path className="curve-line" d="M0 122 L30 120 L58 106 L82 111 L110 98 L136 91 L165 73 L190 94 L214 86 L240 91 L268 84 L296 80 L326 72 L354 79 L382 66 L410 70 L438 63 L468 74 L492 59 L520 51 L548 57 L580 43 L608 50 L635 36 L664 47 L692 41 L724 44 L760 26" />
        <line x1="0" y1="122" x2="760" y2="122" />
      </svg>
    </div>
  );
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "muted",
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  tone?: "success" | "warning" | "danger" | "muted";
}) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <div className="metric-icon">
        <Icon size={18} />
      </div>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function ActionButton({
  icon: Icon,
  label,
  onClick,
  busy,
  tone = "secondary",
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  busy?: boolean;
  tone?: "primary" | "secondary" | "danger";
}) {
  return (
    <button className={`action-button tone-${tone}`} type="button" disabled={busy} onClick={onClick}>
      <Icon size={16} />
      <span>{busy ? "处理中" : label}</span>
    </button>
  );
}

function HotAssetsTable({ items }: { items: HotAsset[] }) {
  if (!items.length) {
    return <EmptyState title="暂无热门币种" text="点击刷新信号后查看广场热度与市场动能排行。" />;
  }
  return (
    <div className="table-shell">
      <table>
        <thead>
            <tr>
              <th>#</th>
              <th></th>
              <th>币种</th>
              <th>综合分</th>
              <th>市场分</th>
              <th>广场分</th>
            <th>24h 涨幅</th>
            <th>波动率</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={`${item.symbol || item.asset || index}-${index}`} className={index === 0 ? "is-leader" : ""}>
              <td className="mono muted">{index + 1}</td>
              <td className="favorite-cell"><Star size={16} /></td>
              <td className="symbol-cell">
                <span className="coin-avatar">{coinInitial(item.symbol || item.asset)}</span>
                {item.symbol || item.asset || "--"}
              </td>
              <td className="mono accent">{formatScore(item.score)}</td>
              <td className="mono">{formatScore(item.market_score)}</td>
              <td className="mono">
                {formatScore(item.square_score)}
                {item.mentions ? <span className="muted"> ({item.mentions})</span> : null}
              </td>
              <td className="mono positive">
                <span className="trend-wrap">
                  <span>{formatPercent(item.price_change_percent)}</span>
                  <MiniSparkline tone="positive" seed={index} />
                </span>
              </td>
              <td className="mono warning">
                <span className="trend-wrap">
                  <span>{formatPercent(item.volatility_percent)}</span>
                  <MiniSparkline tone="warning" seed={index + 3} />
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MiniSparkline({ tone, seed }: { tone: "positive" | "warning"; seed: number }) {
  const offset = seed % 5;
  const points = [
    `0,30`,
    `8,${28 - offset}`,
    `18,${31 - offset}`,
    `30,${24 - offset}`,
    `42,${21 + offset}`,
    `54,${15 + offset}`,
    `66,${17 - offset}`,
    `78,${10 + offset}`,
    `90,${8 + offset}`,
  ].join(" ");
  return (
    <svg className={`mini-spark tone-${tone}`} viewBox="0 0 90 36" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={points} />
    </svg>
  );
}

function TradesPanel({ stats, trades }: { stats: PerformanceStats | null; trades: TradeItem[] }) {
  const quote = stats?.quote_asset || "USDT";
  const recent = trades.slice(-10).reverse();
  return (
    <div className="stack-panel">
      <div className="stats-grid">
        <StatTile label="完成回合" value={trimNumber(stats?.completed_trades ?? 0, 0)} detail={`胜 ${stats?.wins ?? 0} · 负 ${stats?.losses ?? 0}`} />
        <StatTile label="胜率" value={formatPercent(stats?.win_rate ?? 0)} detail={`盈亏比 ${stats?.profit_factor == null ? "--" : trimNumber(stats.profit_factor, 2, 2)}`} />
        <StatTile label="总盈亏" value={signedMoney(stats?.total_pnl ?? 0, quote)} detail={`最大回撤 ${formatMoney(stats?.max_drawdown ?? 0, quote)}`} tone={Number(stats?.total_pnl || 0) < 0 ? "danger" : "success"} />
        <StatTile label="平均盈亏" value={signedMoney(stats?.avg_pnl ?? 0, quote)} detail={`平均收益 ${signedPercent(stats?.avg_return_pct ?? 0)}`} tone={Number(stats?.avg_pnl || 0) < 0 ? "danger" : "success"} />
        <StatTile label="最佳交易" value={signedMoney(stats?.best_trade ?? 0, quote)} detail="单笔最大盈利" tone="success" />
        <StatTile label="最差交易" value={signedMoney(stats?.worst_trade ?? 0, quote)} detail="单笔最大亏损" tone="danger" />
        <StatTile label="毛利润" value={formatMoney(stats?.gross_profit ?? 0, quote)} detail={`毛亏损 ${formatMoney(stats?.gross_loss ?? 0, quote)}`} tone="success" />
        <StatTile label="当前连续" value={trimNumber(stats?.current_streak ?? 0, 0)} detail={streakLabel(stats?.current_streak_type)} />
      </div>
      {!recent.length ? (
        <EmptyState title="暂无交易记录" text="模拟或实盘成交后，这里会显示最近 10 笔动作。" />
      ) : (
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>模式</th>
                <th>动作</th>
                <th>标的</th>
                <th>数量</th>
                <th>价格</th>
                <th>手续费</th>
                <th>成交额</th>
              </tr>
            </thead>
            <tbody>
              {recent.map((item, index) => {
                const action = item.action || "";
                const isBuy = action.includes("BUY");
                const isDryRun = Boolean(item.dry_run);
                return (
                  <tr key={`${item.ts || "trade"}-${index}`}>
                    <td>{formatTime(item.ts)}</td>
                    <td>
                      <span className={`pill ${isDryRun ? "tone-warning" : "tone-danger"}`}>{isDryRun ? "模拟" : "实盘"}</span>
                    </td>
                    <td>
                      <span className={`pill ${isBuy ? "tone-success" : "tone-danger"}`}>{actionLabel(action)}</span>
                    </td>
                    <td className="symbol-cell">{item.symbol || "--"}</td>
                    <td className="mono">{formatQty(item.quantity)}</td>
                    <td className="mono">{formatPrice(item.price)}</td>
                    <td className="mono">{formatMoney(item.fee_amount, item.fee_asset || "")}</td>
                    <td className="mono">{formatMoney(tradeAmount(item), "")}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function StatTile({
  label,
  value,
  detail,
  tone = "muted",
}: {
  label: string;
  value: string;
  detail: string;
  tone?: "success" | "danger" | "muted";
}) {
  return (
    <div className={`stat-tile tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </div>
  );
}

function DiagnosticsPanel({ diagnostics }: { diagnostics: Diagnostics | null }) {
  if (!diagnostics) {
    return <EmptyState title="暂无诊断结果" text="点击诊断广场后查看 Binance Square 抓取与解析状态。" />;
  }
  const urls = diagnostics.urls || [];
  const samples = diagnostics.samples || [];
  return (
    <div className="diagnostics-layout">
      <div className="diagnostic-summary">
        <span>模式</span>
        <strong>{diagnostics.mode || "--"}</strong>
        <p>
          有效帖子 {diagnostics.total_posts ?? 0}
          {diagnostics.raw_posts !== undefined ? ` · 原始 ${diagnostics.raw_posts}` : ""}
          {diagnostics.filtered_out_posts !== undefined ? ` · 过滤 ${diagnostics.filtered_out_posts}` : ""}
          {diagnostics.browser_posts_raw !== undefined ? ` · 浏览器 ${diagnostics.browser_posts_raw}` : ""}
        </p>
        {diagnostics.browser_error ? <p className="negative">浏览器错误：{diagnostics.browser_error}</p> : null}
        {diagnostics.hint ? <p className="warning">{diagnostics.hint}</p> : null}
      </div>
      {urls.length ? (
        <div className="table-shell">
          <table>
            <thead>
              <tr>
                <th>URL</th>
                <th>结果</th>
              </tr>
            </thead>
            <tbody>
              {urls.map((item, index) => (
                <tr key={`${item.url || "url"}-${index}`}>
                  <td className="url-cell">{item.url || "--"}</td>
                  <td>
                    HTTP {item.status_code ?? "--"} · 页面 {item.content_length ?? 0} 字符 · JSON {item.json_posts ?? 0} · HTML {item.html_posts ?? 0}
                    {item.error ? <span className="negative"> · {item.error}</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <div className="sample-list">
        {samples.length ? (
          samples.map((sample, index) => (
            <article className="sample-post" key={`${sample.title || "sample"}-${index}`}>
              <strong>{sample.title || "帖子样例"}</strong>
              <p>{sample.text || "--"}</p>
            </article>
          ))
        ) : (
          <EmptyState title="没有帖子样例" text="当前诊断没有解析到可展示的帖子内容。" />
        )}
      </div>
    </div>
  );
}

function LogsPanel({ logs, requestError }: { logs: string[]; requestError: string }) {
  return (
    <pre className="log-panel">
      {requestError ? `${requestError}\n\n` : ""}
      {logs.length ? logs.join("\n") : "等待日志..."}
    </pre>
  );
}

function SettingsPanel({
  activeTab,
  settings,
  setActiveTab,
  setFixedStopEdited,
  updateSetting,
}: {
  activeTab: SettingsTabKey;
  settings: SettingsState;
  setActiveTab: (tab: SettingsTabKey) => void;
  setFixedStopEdited: (value: boolean) => void;
  updateSetting: <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => void;
}) {
  function Field({
    name,
    label,
    help,
    type = "text",
    min,
    step,
    placeholder,
    full = false,
  }: {
    name: keyof SettingsState;
    label: string;
    help?: string;
    type?: string;
    min?: string;
    step?: string;
    placeholder?: string;
    full?: boolean;
  }) {
    return (
      <label className={`field ${full ? "is-full" : ""}`}>
        <span>{label}</span>
        <input
          type={type}
          min={min}
          step={step}
          value={String(settings[name])}
          placeholder={placeholder}
          onChange={(event) => {
            if (name === "fixed_stop_loss_usdt") {
              setFixedStopEdited(true);
            }
            updateSetting(name, event.target.value as never);
          }}
        />
        {help ? <small>{help}</small> : null}
      </label>
    );
  }

  function Toggle({ name, label }: { name: keyof SettingsState; label: string }) {
    return (
      <label className="toggle-row">
        <input
          type="checkbox"
          checked={Boolean(settings[name])}
          onChange={(event) => updateSetting(name, event.target.checked as never)}
        />
        <span>{label}</span>
      </label>
    );
  }

  return (
    <div className="settings-layout">
      <aside className="settings-menu">
        {SETTINGS_TABS.map((item) => (
          <button
            key={item.key}
            className={activeTab === item.key ? "is-active" : ""}
            type="button"
            onClick={() => setActiveTab(item.key)}
          >
            {item.label}
          </button>
        ))}
      </aside>
      <div className="settings-content">
        {activeTab === "basic" && (
          <SettingsSection title="基础交易" description="控制交易计价、单笔投入和本地状态文件。">
            <Field name="quote_asset" label="计价币种" />
            <Field name="order_quote_amount" label="单笔金额" type="number" min="1" step="1" />
            <Field name="state_file" label="状态文件" full />
          </SettingsSection>
        )}
        {activeTab === "signal" && (
          <SettingsSection title="信号筛选" description="候选进入排序前必须满足的行情和广场热度条件。">
            <Field name="min_price_change_percent" label="最低涨幅 %" type="number" step="0.1" />
            <Field name="min_volatility_percent" label="最低波动 %" type="number" step="0.1" />
            <Field name="min_quote_volume" label="最低成交额" type="number" min="0" step="100000" full />
            <Field name="top_post_limit" label="热门帖子数" type="number" min="1" step="1" />
            <Field name="top_coin_limit" label="热门币种数" type="number" min="1" step="1" />
          </SettingsSection>
        )}
        {activeTab === "scope" && (
          <SettingsSection title="交易范围" description="控制允许交易的币种、大盘环境过滤和实盘账户同步。">
            <Field name="asset_whitelist" label="白名单" placeholder="BTC,ETH,SOL 或 SOLUSDT" help="填写后只交易这些币种；留空表示不限制。" full />
            <Field name="asset_blacklist" label="黑名单" placeholder="USDC,FDUSD 或 OPNUSDT" help="这些币种永不新开仓，优先级高于候选排序。" full />
            <Field name="market_filter_assets" label="大盘过滤币种" help="用于判断大盘环境，默认 BTC 和 ETH。" />
            <Field name="market_filter_min_change_pct" label="大盘最低涨幅 %" type="number" step="0.1" help="低于该 24h 涨幅时暂停追涨开仓。" />
            <div className="toggle-grid">
              <Toggle name="market_filter_enabled" label="启用 BTC/ETH 大盘过滤" />
              <Toggle name="market_filter_require_all" label="要求全部大盘币满足" />
              <Toggle name="account_sync_enabled" label="实盘成交后账户同步" />
            </div>
          </SettingsSection>
        )}
        {activeTab === "risk" && (
          <SettingsSection title="风控退出" description="控制止损、止盈、保本、移动止盈和开仓节流。">
            <Field name="initial_stop_loss_pct" label="初始止损 %" type="number" min="0.1" step="0.1" />
            <Field name="take_profit_pct" label="止盈 %" type="number" min="0" step="0.1" />
            <Field name="breakeven_trigger_pct" label="保本触发 %" type="number" min="0" step="0.1" help="最高价达到该涨幅后，把动态止损抬到成本附近；填 0 关闭。" />
            <Field name="breakeven_offset_pct" label="保本偏移 %" type="number" step="0.1" help="保本止损相对开仓价的偏移，0 表示刚好成本价。" />
            <Field name="trailing_start_pct" label="移动止盈启动 %" type="number" min="0" step="0.1" help="最高价达到该涨幅后启用移动止盈。" />
            <Field name="trailing_stop_pct" label="移动止盈回撤 %" type="number" min="0" step="0.1" help="从最高价回撤该比例时卖出；填 0 关闭。" />
            <Field name="fixed_stop_loss_usdt" label="固定止损 USDT" type="number" min="1" step="1" help="仅在固定止损模式启用后生效；建议为单笔金额的 10%-25%。" />
            <Field name="fixed_stop_equity_usdt" label="权益触发 USDT" type="number" min="0" step="1" help="留空则不按账户权益切换固定止损。" />
            <Field name="cooldown_minutes" label="冷却分钟" type="number" min="0" step="1" help="同一币种卖出后暂停重新开仓；填 0 关闭。" />
            <Field name="max_daily_trades" label="每日最大开仓" type="number" min="0" step="1" help="按 UTC 日期统计买入次数；填 0 关闭。" />
            <Field name="max_daily_loss_usdt" label="每日最大亏损 USDT" type="number" min="0" step="1" help="已实现亏损达到后停止新开仓；填 0 关闭。" full />
            <div className="toggle-grid">
              <Toggle name="fixed_stop_after_first_round_trip" label="首回合后固定止损" />
            </div>
          </SettingsSection>
        )}
        {activeTab === "cost" && (
          <SettingsSection title="交易成本" description="用于 dry-run 估算真实成交偏差和手续费。">
            <Field name="fee_rate_pct" label="手续费 %" type="number" min="0" step="0.01" help="dry-run 估算手续费，影响模拟成本和绩效统计。" />
            <Field name="slippage_pct" label="滑点 %" type="number" min="0" step="0.01" help="dry-run 买入上浮、卖出下调，用于贴近真实成交。" />
          </SettingsSection>
        )}
        {activeTab === "runtime" && (
          <SettingsSection title="运行模式" description="控制循环频率、签名窗口、测试网、实盘和广场抓取方式。">
            <Field name="poll_seconds" label="轮询秒数" type="number" min="5" step="1" />
            <Field name="recv_window_ms" label="签名窗口 ms" type="number" min="1000" step="100" />
            <div className="toggle-grid">
              <Toggle name="testnet" label="Testnet" />
              <Toggle name="live" label="Live 实盘" />
              <Toggle name="square_browser_mode" label="浏览器抓广场" />
            </div>
          </SettingsSection>
        )}
      </div>
    </div>
  );
}

function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <section className="settings-section">
      <div className="section-heading">
        <p className="eyebrow">Configuration</p>
        <h2>{title}</h2>
        <span>{description}</span>
      </div>
      <div className="settings-grid">{children}</div>
    </section>
  );
}

function EmptyState({ title, text }: { title: string; text: string }) {
  return (
    <div className="empty-state">
      <AlertCircle size={22} />
      <strong>{title}</strong>
      <p>{text}</p>
    </div>
  );
}

function settingsFromConfig(config: ConfigPayload): SettingsState {
  return {
    ...DEFAULT_SETTINGS,
    quote_asset: textValue(config.quote_asset) || DEFAULT_SETTINGS.quote_asset,
    order_quote_amount: textValue(config.order_quote_amount) || DEFAULT_SETTINGS.order_quote_amount,
    state_file: textValue(config.state_file) || DEFAULT_SETTINGS.state_file,
    min_price_change_percent: textValue(config.min_price_change_percent) || DEFAULT_SETTINGS.min_price_change_percent,
    min_volatility_percent: textValue(config.min_volatility_percent) || DEFAULT_SETTINGS.min_volatility_percent,
    min_quote_volume: textValue(config.min_quote_volume) || DEFAULT_SETTINGS.min_quote_volume,
    top_post_limit: textValue(config.top_post_limit) || DEFAULT_SETTINGS.top_post_limit,
    top_coin_limit: textValue(config.top_coin_limit) || DEFAULT_SETTINGS.top_coin_limit,
    asset_whitelist: textValue(config.asset_whitelist),
    asset_blacklist: textValue(config.asset_blacklist),
    market_filter_assets: textValue(config.market_filter_assets) || DEFAULT_SETTINGS.market_filter_assets,
    market_filter_min_change_pct: textValue(config.market_filter_min_change_pct) || DEFAULT_SETTINGS.market_filter_min_change_pct,
    initial_stop_loss_pct: textValue(config.initial_stop_loss_pct) || DEFAULT_SETTINGS.initial_stop_loss_pct,
    take_profit_pct: textValue(config.take_profit_pct) || DEFAULT_SETTINGS.take_profit_pct,
    breakeven_trigger_pct: textValue(config.breakeven_trigger_pct) || DEFAULT_SETTINGS.breakeven_trigger_pct,
    breakeven_offset_pct: textValue(config.breakeven_offset_pct) || DEFAULT_SETTINGS.breakeven_offset_pct,
    trailing_start_pct: textValue(config.trailing_start_pct) || DEFAULT_SETTINGS.trailing_start_pct,
    trailing_stop_pct: textValue(config.trailing_stop_pct) || DEFAULT_SETTINGS.trailing_stop_pct,
    fixed_stop_loss_usdt: textValue(config.fixed_stop_loss_usdt) || DEFAULT_SETTINGS.fixed_stop_loss_usdt,
    fixed_stop_equity_usdt: textValue(config.fixed_stop_equity_usdt),
    cooldown_minutes: textValue(config.cooldown_minutes) || DEFAULT_SETTINGS.cooldown_minutes,
    max_daily_trades: textValue(config.max_daily_trades) || DEFAULT_SETTINGS.max_daily_trades,
    max_daily_loss_usdt: textValue(config.max_daily_loss_usdt) || DEFAULT_SETTINGS.max_daily_loss_usdt,
    fee_rate_pct: textValue(config.fee_rate_pct) || DEFAULT_SETTINGS.fee_rate_pct,
    slippage_pct: textValue(config.slippage_pct) || DEFAULT_SETTINGS.slippage_pct,
    poll_seconds: textValue(config.poll_seconds) || DEFAULT_SETTINGS.poll_seconds,
    recv_window_ms: textValue(config.recv_window_ms) || DEFAULT_SETTINGS.recv_window_ms,
    testnet: textValue(config.base_url).includes("testnet"),
    live: config.dry_run === false,
    square_browser_mode: Boolean(config.square_browser_mode),
    fixed_stop_after_first_round_trip: Boolean(config.fixed_stop_after_first_round_trip),
    market_filter_enabled: Boolean(config.market_filter_enabled),
    market_filter_require_all: Boolean(config.market_filter_require_all),
    account_sync_enabled: config.account_sync_enabled !== false,
  };
}

function formatDefaultFixedStop(value: number): string {
  const rounded = Math.max(1, value * 0.2);
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/\.?0+$/, "");
}

function positionLabel(position: Position | null, snapshot: PositionSnapshot | null): string {
  if (!position?.symbol) {
    return "--";
  }
  return `${snapshot?.mode_label || "持仓"} ${position.symbol}`;
}

function positionDetail(position: Position | null, snapshot: PositionSnapshot | null): string {
  if (!position?.symbol) {
    return "暂无持仓";
  }
  const parts = [
    `数量 ${formatQty(snapshot?.quantity ?? position.quantity)}`,
    `成本 ${formatPrice(snapshot?.entry_price ?? position.entry_price)}`,
  ];
  if (snapshot?.current_price) {
    parts.push(`现价 ${formatPrice(snapshot.current_price)}`);
  }
  if (snapshot?.highest_price) {
    parts.push(`最高 ${formatPrice(snapshot.highest_price)}`);
  }
  return parts.join(" · ");
}

function riskSummary(snapshot: PositionSnapshot | null, guard: EntryGuardSnapshot | null, roundTrips: unknown): string {
  const parts = [];
  if (snapshot?.stop_triggered) {
    parts.push("已触发止损");
  } else if (snapshot?.take_profit_triggered) {
    parts.push("已触发止盈");
  } else {
    parts.push("风控正常");
  }
  if (snapshot?.active_stop_mode) {
    parts.push(stopModeLabel(snapshot.active_stop_mode));
  }
  if (snapshot?.dynamic_stop_price) {
    parts.push(`止损 ${formatPrice(snapshot.dynamic_stop_price)}`);
  }
  if (snapshot?.stop_distance_pct) {
    parts.push(`距止损 ${formatPercent(snapshot.stop_distance_pct)}`);
  }
  if (guard) {
    const tradeLimit = Number(guard.max_daily_trades || 0);
    parts.push(tradeLimit > 0 ? `今日开仓 ${guard.buy_count ?? 0}/${guard.max_daily_trades}` : `今日开仓 ${guard.buy_count ?? 0}`);
    if (guard.entry_blocked) {
      parts.push("暂停新开仓");
    }
  }
  parts.push(`交易回合 ${roundTrips ?? 0}`);
  return parts.join(" · ");
}

function streakLabel(type?: string): string {
  if (type === "win") {
    return "连胜";
  }
  if (type === "loss") {
    return "连亏";
  }
  return "连续";
}

function coinInitial(symbol?: string): string {
  const clean = String(symbol || "?").replace(/USDT$/, "");
  return clean.slice(0, 1).toUpperCase() || "?";
}

export default App;
