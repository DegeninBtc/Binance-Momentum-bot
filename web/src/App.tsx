import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties, ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Activity,
  AlertCircle,
  BarChart3,
  BellRing,
  BookOpen,
  CheckCircle2,
  CircleDollarSign,
  Clock3,
  Database,
  ExternalLink,
  Flame,
  Home,
  KeyRound,
  Moon,
  Play,
  Power,
  RefreshCw,
  Search,
  Send,
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
import { dashboardHeaders, fetchMarketChart, fetchStatus, fetchTrades, postAction, postPositionClose } from "./api";
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
  AccountRiskSnapshot,
  ChartRangeKey,
  DashboardSecurity,
  DashboardStatus,
  Diagnostics,
  DiagnosticsPost,
  EntryConfirmation,
  EntryGuardSnapshot,
  HotAsset,
  MarketChart,
  PerformanceStats,
  Position,
  PositionSnapshot,
  Primitive,
  SafetySnapshot,
  SquareConfidence,
  SettingsState,
  SettingsTabKey,
  TabKey,
  TradeItem,
  TradeJournalPage,
  TradeRoundTrip,
} from "./types";

const DEFAULT_SETTINGS: SettingsState = {
  quote_asset: "USDT",
  trade_market_mode: "futures_preferred",
  futures_margin_type: "ISOLATED",
  order_quote_amount: "50",
  dry_run_initial_equity_usdt: "750",
  max_open_positions: "15",
  leverage_multiplier: "3",
  contract_max_margin_loss_pct: "20",
  liquidation_stop_buffer_pct: "2",
  contract_simulation_enabled: true,
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
  initial_stop_loss_pct: "4",
  take_profit_pct: "0",
  breakeven_trigger_pct: "3",
  breakeven_offset_pct: "0.2",
  trailing_start_pct: "6",
  trailing_stop_pct: "3",
  fixed_stop_loss_usdt: "10",
  fixed_stop_equity_usdt: "",
  cooldown_minutes: "30",
  max_daily_trades: "5",
  max_daily_loss_usdt: "25",
  max_total_exposure_pct: "0",
  max_symbol_exposure_pct: "0",
  max_consecutive_losses: "0",
  max_intraday_drawdown_pct: "0",
  risk_per_trade_pct: "0",
  fee_rate_pct: "0.1",
  slippage_pct: "0.05",
  poll_seconds: "300",
  recv_window_ms: "5000",
  testnet: false,
  live: false,
  square_browser_mode: true,
  square_diagnostic_limit: "10",
  telegram_bot_token: "",
  telegram_chat_id: "",
  dashboard_auth_token: "",
  signal_recording_enabled: true,
  signal_record_file: "signal_records.jsonl",
  telegram_enabled: false,
  fixed_stop_after_first_round_trip: false,
  market_filter_enabled: false,
  market_filter_require_all: false,
  account_sync_enabled: true,
  kline_confirmation_enabled: true,
  min_square_confidence_score: "35",
  max_spread_bps: "50",
  min_orderbook_depth_usdt: "1000",
  exchange_protection_enabled: true,
  oco_stop_limit_slippage_pct: "0.5",
};

const TAB_ITEMS: Array<{ key: TabKey; label: string; icon: LucideIcon }> = [
  { key: "positions", label: "当前仓位", icon: Wallet },
  { key: "hot", label: "热门币种", icon: Flame },
  { key: "diag", label: "广场诊断", icon: Search },
  { key: "strategy", label: "策略", icon: Activity },
  { key: "favorites", label: "收藏", icon: Star },
  { key: "trades", label: "交易记录", icon: CircleDollarSign },
  { key: "security", label: "安全状态", icon: Shield },
  { key: "logs", label: "日志", icon: Database },
  { key: "notify", label: "通知", icon: BellRing },
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

const STRATEGY_PRESETS = {
  conservative: {
    min_price_change_percent: "4",
    min_volatility_percent: "6",
    min_quote_volume: "10000000",
    cooldown_minutes: "60",
    max_daily_trades: "3",
    max_daily_loss_usdt: "15",
    max_open_positions: "1",
    fee_rate_pct: "0.1",
    slippage_pct: "0.08",
  },
  standard: {
    min_price_change_percent: "3",
    min_volatility_percent: "5",
    min_quote_volume: "5000000",
    cooldown_minutes: "30",
    max_daily_trades: "5",
    max_daily_loss_usdt: "25",
    max_open_positions: "15",
    leverage_multiplier: "3",
    fee_rate_pct: "0.1",
    slippage_pct: "0.05",
  },
  aggressive: {
    min_price_change_percent: "2",
    min_volatility_percent: "4",
    min_quote_volume: "2500000",
    cooldown_minutes: "15",
    max_daily_trades: "8",
    max_daily_loss_usdt: "40",
    max_open_positions: "20",
    leverage_multiplier: "5",
    fee_rate_pct: "0.1",
    slippage_pct: "0.08",
  },
} satisfies Record<string, Partial<SettingsState>>;

type StrategyPresetKey = keyof typeof STRATEGY_PRESETS;

const PRESET_LABELS: Record<StrategyPresetKey, string> = {
  conservative: "保守",
  standard: "标准",
  aggressive: "激进",
};

const CHART_RANGES: ChartRangeKey[] = ["1H", "6H", "24H", "7D", "30D"];
const SETTINGS_STORAGE_KEY = "dashboard-settings";
const SETTINGS_BROWSER_DEFAULT_MIGRATION_KEY = "dashboard-settings-browser-default-v1";
const SETTINGS_CONTRACT_DEFAULT_MIGRATION_KEY = "dashboard-settings-contract-default-v1";
const SETTINGS_PRESET_DEFAULT_MIGRATION_KEY = "dashboard-settings-preset-default-v1";
const FAVORITES_STORAGE_KEY = "dashboard-favorite-symbols";

function App() {
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [settings, setSettings] = useState<SettingsState>(DEFAULT_SETTINGS);
  const [settingsHydrated, setSettingsHydrated] = useState(false);
  const [fixedStopEdited, setFixedStopEdited] = useState(false);
  const [activePreset, setActivePreset] = useState<StrategyPresetKey>("standard");
  const [activeTab, setActiveTab] = useState<TabKey>("positions");
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsTabKey>("basic");
  const [busyPath, setBusyPath] = useState("");
  const [requestError, setRequestError] = useState("");
  const [theme, setTheme] = useState(() => localStorage.getItem("dashboard-theme") || "light");
  const [chartRange, setChartRange] = useState<ChartRangeKey>("24H");
  const [marketChart, setMarketChart] = useState<MarketChart | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const [positionsExpanded, setPositionsExpanded] = useState(false);
  const [favoriteSymbols, setFavoriteSymbols] = useState<string[]>(() => loadFavoriteSymbols());
  const [tradeView, setTradeView] = useState<"round_trips" | "events">("round_trips");
  const [tradeOffset, setTradeOffset] = useState(0);
  const [tradePage, setTradePage] = useState<TradeJournalPage | null>(null);
  const [tradeLoading, setTradeLoading] = useState(false);
  const [tradeError, setTradeError] = useState("");

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
      setSettings({ ...settingsFromConfig(status.config), ...loadSavedSettings() });
      setSettingsHydrated(true);
    }
  }, [settingsHydrated, status?.config]);

  const loadTrades = useCallback(async () => {
    setTradeLoading(true);
    setTradeError("");
    try {
      const page = await fetchTrades(tradeView, 25, tradeOffset);
      setTradePage(page);
    } catch (error) {
      setTradeError(error instanceof Error ? error.message : "交易记录加载失败");
    } finally {
      setTradeLoading(false);
    }
  }, [tradeOffset, tradeView]);

  useEffect(() => {
    if (activeTab === "trades") {
      loadTrades();
    }
  }, [activeTab, loadTrades, status?.state?.trade_journal?.event_count, status?.state?.trade_journal?.round_trip_count]);

  useEffect(() => {
    localStorage.setItem(FAVORITES_STORAGE_KEY, JSON.stringify(favoriteSymbols));
  }, [favoriteSymbols]);

  const config = status?.config || {};
  const state = status?.state || {};
  const signal = status?.last_signal || {};
  const candidate = signal.candidate || null;
  const positions = state.positions?.length ? state.positions : state.position ? [state.position] : [];
  const snapshots = state.position_snapshots?.length ? state.position_snapshots : state.position_snapshot ? [state.position_snapshot] : [];
  const positionViews = useMemo(() => buildPositionViews(positions, snapshots), [positions, snapshots]);
  const position = state.position || positions[0] || null;
  const snapshot = state.position_snapshot || snapshots[0] || null;
  const guard = state.entry_guard_snapshot || null;
  const performance = state.performance_stats || null;
  const trades = state.trade_log || [];
  const safety = state.safety_snapshot || null;
  const entryConfirmation = state.entry_confirmation || signal.entry_confirmation || null;
  const squareConfidence = state.square_confidence || signal.square_confidence || null;
  const accountRisk = state.account_risk_snapshot || null;
  const diagnostics = status?.last_diagnostics || null;
  const loopSnapshot = status?.loop_snapshot || null;
  const dashboardSecurity = status?.dashboard_security || null;
  const readOnlyMode = Boolean(dashboardSecurity?.read_only);
  const readOnlyReason = readOnlyMode ? "Dashboard read-only mode is enabled" : "";
  const hasError = Boolean(requestError || status?.last_error);
  const running = Boolean(status?.running);
  const runningMode = textValue(status?.mode);
  const previewBusy = busyPath === "/api/preview" || (running && runningMode === "preview");
  const diagnosticsBusy = busyPath === "/api/square-diagnose" || busyPath === "/api/update-signal-returns" || (running && runningMode === "square-diagnostics");
  const taskLocked = running && !["preview", "square-diagnostics"].includes(runningMode);
  const taskLockReason = taskLocked ? `当前已有任务运行中：${runningMode}。请先停止或等待完成。` : readOnlyReason;
  const keysLoaded = Boolean(config.api_key_loaded && config.api_secret_loaded);
  const liveMode = settings.live || config.dry_run === false;
  const quoteAsset = snapshot?.quote_asset || textValue(config.quote_asset) || settings.quote_asset || "USDT";
  const heroSymbol = candidate?.symbol || position?.symbol || "OPNUSDT";
  const updatedAt = loopSnapshot?.last_cycle_finished_at || status?.last_finished_at || status?.last_started_at || "--";
  const loopDetail = loopSnapshot?.last_cycle_note
    ? `${loopSnapshot.last_cycle_note}${loopSnapshot.next_cycle_eta ? ` · 下轮 ${loopSnapshot.next_cycle_eta}` : ""}`
    : `轮询 ${settings.poll_seconds || "300"} 秒 · 页面 2.5 秒刷新`;
  const totals = useMemo(() => positionTotals(positionViews), [positionViews]);
  const hotAssets = signal.hot_assets || [];
  const favoriteSet = useMemo(() => new Set(favoriteSymbols), [favoriteSymbols]);

  const riskTone = useMemo(() => {
    if (hasError || snapshot?.liquidation_triggered || snapshot?.stop_triggered || guard?.entry_blocked) {
      return "danger";
    }
    if (snapshot?.take_profit_triggered) {
      return "success";
    }
    if (running || liveMode || !keysLoaded) {
      return "warning";
    }
    return "success";
  }, [guard?.entry_blocked, hasError, keysLoaded, liveMode, running, snapshot?.liquidation_triggered, snapshot?.stop_triggered, snapshot?.take_profit_triggered]);

  useEffect(() => {
    if (!heroSymbol) {
      setMarketChart(null);
      return;
    }
    let cancelled = false;
    setChartLoading(true);
    setChartError("");
    fetchMarketChart(heroSymbol, chartRange, Boolean(settings.testnet))
      .then((chart) => {
        if (!cancelled) {
          setMarketChart(chart);
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setChartError(error instanceof Error ? error.message : "行情图加载失败");
          setMarketChart(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setChartLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [chartRange, heroSymbol, settings.testnet]);

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

  function applyStrategyPreset(name: StrategyPresetKey) {
    const preset = STRATEGY_PRESETS[name];
    setSettings((current) => {
      const next = { ...current, ...preset };
      const amount = Number(next.order_quote_amount);
      if (Number.isFinite(amount) && amount > 0) {
        next.fixed_stop_loss_usdt = formatDefaultFixedStop(amount);
      }
      return next;
    });
    setFixedStopEdited(false);
    setActivePreset(name);
  }

  function openDashboardTab(tab: TabKey, settingsTab?: SettingsTabKey) {
    setActiveTab(tab);
    if (settingsTab) {
      setActiveSettingsTab(settingsTab);
    }
  }

  function toggleFavorite(symbol: string) {
    const normalized = normalizeSymbol(symbol);
    if (!normalized) {
      return;
    }
    setFavoriteSymbols((current) => {
      if (current.includes(normalized)) {
        return current.filter((item) => item !== normalized);
      }
      return [...current, normalized].sort();
    });
  }

  function liveConfirmationMessage(path: string) {
    if (!settings.live || !["/api/run-once", "/api/start-loop", "/api/manual-close"].includes(path)) {
      return "";
    }
    if (path === "/api/manual-close") {
      return "Confirm LIVE market close for the current position? This may place a real futures reduce-only or spot sell order.";
    }
    if (path === "/api/start-loop") {
      return "Confirm LIVE loop start? Future cycles may place real futures or spot orders.";
    }
    return "Confirm LIVE run once? This cycle may place a real futures or spot order.";
  }

  async function submit(path: string, nextTab?: TabKey) {
    const confirmMessage = liveConfirmationMessage(path);
    const liveConfirmed = Boolean(confirmMessage);
    if (confirmMessage && !window.confirm(confirmMessage)) {
      return;
    }
    setBusyPath(path);
    if (nextTab) {
      setActiveTab(nextTab);
    }
    try {
      const data = await postAction(path, liveConfirmed ? { ...settings, live_confirmed: true } : settings);
      setStatus(data);
      setRequestError("");
      window.setTimeout(refresh, 800);
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "Request failed");
    } finally {
      setBusyPath("");
    }
  }

  async function closePosition(symbol: string, quantity: string) {
    const quantityText = trimNumber(quantity, 8);
    const message = settings.live
      ? `确认实盘市价平仓 ${symbol} 数量 ${quantityText}？这个操作会真实下单。`
      : `确认模拟平仓 ${symbol} 数量 ${quantityText}？`;
    if (!window.confirm(message)) {
      return;
    }
    const busyKey = `/api/close-position:${symbol}`;
    setBusyPath(busyKey);
    try {
      const data = await postPositionClose({ ...settings, symbol, close_quantity: quantity, live_confirmed: settings.live ? true : undefined });
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
          <button className={activeTab === "positions" ? "is-active" : ""} type="button" title="首页" onClick={() => openDashboardTab("positions")}>
            <Home size={20} />
          </button>
          <button className={activeTab === "hot" ? "is-active" : ""} type="button" title="行情" onClick={() => openDashboardTab("hot")}>
            <BarChart3 size={20} />
          </button>
          <button className={activeTab === "strategy" ? "is-active" : ""} type="button" title="策略" onClick={() => openDashboardTab("strategy")}>
            <Activity size={20} />
          </button>
          <button className={activeTab === "favorites" ? "is-active" : ""} type="button" title="收藏" onClick={() => openDashboardTab("favorites")}>
            <Star size={20} />
          </button>
          <button className={activeTab === "trades" ? "is-active" : ""} type="button" title="记录" onClick={() => openDashboardTab("trades")}>
            <Database size={20} />
          </button>
          <button className={activeTab === "logs" ? "is-active" : ""} type="button" title="说明" onClick={() => openDashboardTab("logs")}>
            <BookOpen size={20} />
          </button>
          <button className={activeTab === "notify" ? "is-active" : ""} type="button" title="通知" onClick={() => openDashboardTab("notify")}>
            <BellRing size={20} />
          </button>
          <button className={activeTab === "settings" ? "is-active" : ""} type="button" title="设置" onClick={() => openDashboardTab("settings", "basic")}>
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
          <button
            className={`icon-button top-notify-button ${activeTab === "notify" ? "is-active" : ""}`}
            type="button"
            title="通知设置"
            onClick={() => openDashboardTab("notify")}
          >
            <BellRing size={17} />
          </button>
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
                <a className="hero-symbol-link" href={tradingViewChartUrl(heroSymbol)} target="_blank" rel="noreferrer">
                  <h1>{heroSymbol}</h1>
                </a>
              </div>
              <button
                className={`hero-star-button ${favoriteSet.has(normalizeSymbol(heroSymbol)) ? "is-active" : ""}`}
                type="button"
                title={favoriteSet.has(normalizeSymbol(heroSymbol)) ? "取消收藏" : "收藏币种"}
                onClick={() => toggleFavorite(heroSymbol)}
              >
                <Star size={28} fill="currentColor" />
              </button>
            </div>
            <div className="hero-score-row">
              <HeroScore label="综合分" value={formatScore(candidate?.combined_score ?? candidate?.price_change_percent ?? 0)} />
              <HeroScore label="24h 涨幅" value={formatPercent(candidate?.price_change_percent ?? 0)} tone="positive" />
              <HeroScore label="波动率" value={formatPercent(candidate?.volatility_percent ?? 0)} tone="warning" />
            </div>
            <MarketCurve chart={marketChart} loading={chartLoading} error={chartError} chartUrl={tradingViewChartUrl(heroSymbol)} />
            <div className="range-tabs" aria-label="行情周期">
              {CHART_RANGES.map((range) => (
                <button
                  className={chartRange === range ? "is-active" : ""}
                  type="button"
                  key={range}
                  onClick={() => setChartRange(range)}
                >
                  {range}
                </button>
              ))}
            </div>
          </div>
          <div className="hero-side">
            <div className="hero-meta">
              <Database className="source-watermark" size={38} />
              <span>数据源</span>
              <strong>{signal.source || "--"}</strong>
              <small>{signal.checked_at ? `检查于 ${signal.checked_at}` : signal.note || "等待首次刷新"}</small>
            </div>
            <div className={`hero-meta ${hasError ? "tone-danger" : ""}`}>
              <Clock3 className="source-watermark" size={38} />
              <span>最后更新</span>
              <strong>{updatedAt}</strong>
              <small>{requestError || status?.last_error || loopDetail}</small>
            </div>
          </div>
        </section>

        <section className="overview-grid">
          <PositionsSummaryCard
            positions={positionViews}
            expanded={positionsExpanded}
            onToggle={() => setPositionsExpanded((value) => !value)}
            onOpenTab={() => setActiveTab("positions")}
          />
          <MetricCard
            label="浮动盈亏"
            value={totals.marketValue !== null ? `${signedMoney(totals.unrealizedPnl, quoteAsset)} · ${signedPercent(totals.unrealizedPnlPct)}` : "--"}
            detail={totals.marketValue !== null ? `市值 ${formatMoney(totals.marketValue, quoteAsset)} · 持仓 ${positionViews.length}` : snapshot?.price_error || "等待当前价格"}
            icon={totals.unrealizedPnl !== null && totals.unrealizedPnl < 0 ? TrendingDown : TrendingUp}
            tone={totals.unrealizedPnl !== null && totals.unrealizedPnl < 0 ? "danger" : "success"}
            onClick={() => openDashboardTab("positions")}
          />
          <MetricCard
            label="运行 / 风控"
            value={status?.mode || "idle"}
            detail={riskSummary(snapshot, guard, state.completed_round_trips)}
            icon={Target}
            tone={riskTone}
            onClick={() => openDashboardTab("settings", "risk")}
          />
        </section>

        <section className="command-panel">
          <div className="command-title">
            <p className="eyebrow">Actions</p>
            <h2>操作中枢</h2>
          </div>
          <div className="command-grid">
            <ActionButton icon={RefreshCw} label="刷新信号" busy={previewBusy} disabled={readOnlyMode} title={taskLockReason} onClick={() => submit("/api/preview", "hot")} />
            <ActionButton icon={Search} label="诊断广场" busy={diagnosticsBusy} disabled={readOnlyMode} title={taskLockReason} onClick={() => submit("/api/square-diagnose", "diag")} />
            <ActionButton icon={Play} label="执行一次" tone="primary" busy={busyPath === "/api/run-once"} disabled={readOnlyMode || running} title={running ? taskLockReason : readOnlyReason} onClick={() => submit("/api/run-once", "hot")} />
            <ActionButton icon={Activity} label="启动循环" busy={busyPath === "/api/start-loop"} disabled={readOnlyMode || running} title={running ? taskLockReason : readOnlyReason} onClick={() => submit("/api/start-loop")} />
            <ActionButton icon={Square} label="停止" tone="danger" busy={busyPath === "/api/stop"} disabled={readOnlyMode} title={readOnlyReason} onClick={() => submit("/api/stop")} />
            <ActionButton icon={Power} label="手动平仓" tone="danger" busy={busyPath === "/api/manual-close"} disabled={readOnlyMode || running} title={running ? taskLockReason : readOnlyReason} onClick={manualClose} />
            <ActionButton icon={Trash2} label="清空模拟仓位" tone="danger" busy={busyPath === "/api/reset-dry-run-state"} disabled={readOnlyMode || running} title={running ? taskLockReason : readOnlyReason} onClick={resetState} />
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
          {activeTab === "positions" && (
            <PositionsPanel
              positions={positionViews}
              snapshots={snapshots}
              onClosePosition={closePosition}
              busySymbol={busyPath.startsWith("/api/close-position:") ? busyPath.split(":")[1] : ""}
              readOnly={readOnlyMode || running}
              readOnlyReason={running ? taskLockReason : readOnlyReason}
            />
          )}
          {activeTab === "hot" && (
            <HotAssetsTable
              items={hotAssets}
              favoriteSymbols={favoriteSet}
              onToggleFavorite={toggleFavorite}
            />
          )}
          {activeTab === "favorites" && (
            <FavoritesPanel
              favoriteSymbols={favoriteSymbols}
              hotAssets={hotAssets}
              onToggleFavorite={toggleFavorite}
            />
          )}
          {activeTab === "strategy" && (
            <StrategyPanel
              activePreset={activePreset}
              applyStrategyPreset={applyStrategyPreset}
              openSettingsTab={(tab) => openDashboardTab("settings", tab)}
              settings={settings}
            />
          )}
          {activeTab === "trades" && (
            <TradesPanel
              stats={tradePage?.stats || performance}
              fallbackTrades={trades}
              page={tradePage}
              view={tradeView}
              offset={tradeOffset}
              loading={tradeLoading}
              error={tradeError}
              onViewChange={(view) => {
                setTradeView(view);
                setTradeOffset(0);
              }}
              onPageChange={setTradeOffset}
              onRefresh={loadTrades}
            />
          )}
          {activeTab === "security" && (
            <SecurityStatusPanel
              safety={safety}
              liveMode={liveMode}
              entryConfirmation={entryConfirmation}
              squareConfidence={squareConfidence}
              accountRisk={accountRisk}
              dashboardSecurity={dashboardSecurity}
            />
          )}
          {activeTab === "diag" && (
            <DiagnosticsPanel
              diagnostics={diagnostics}
              settings={settings}
              busy={diagnosticsBusy}
              readOnly={readOnlyMode}
              readOnlyReason={taskLockReason}
              updateSetting={updateSetting}
              onDiagnose={() => submit("/api/square-diagnose", "diag")}
              onUpdateReturns={() => submit("/api/update-signal-returns", "diag")}
            />
          )}
          {activeTab === "logs" && <LogsPanel logs={status?.logs || []} requestError={requestError} />}
          {activeTab === "notify" && (
            <NotificationPanel
              settings={settings}
              updateSetting={updateSetting}
            />
          )}
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

function SecurityStatusPanel({
  safety,
  liveMode,
  entryConfirmation,
  squareConfidence,
  accountRisk,
  dashboardSecurity,
}: {
  safety: SafetySnapshot | null;
  liveMode: boolean;
  entryConfirmation: EntryConfirmation | null;
  squareConfidence: SquareConfidence | null;
  accountRisk: AccountRiskSnapshot | null;
  dashboardSecurity: DashboardSecurity | null;
}) {
  return (
    <div className="security-page">
      <SafetyPanel safety={safety} liveMode={liveMode} entryConfirmation={entryConfirmation} squareConfidence={squareConfidence} accountRisk={accountRisk} />
      <DashboardSecurityPanel security={dashboardSecurity} />
    </div>
  );
}

function SafetyPanel({
  safety,
  liveMode,
  entryConfirmation,
  squareConfidence,
  accountRisk,
}: {
  safety: SafetySnapshot | null;
  liveMode: boolean;
  entryConfirmation: EntryConfirmation | null;
  squareConfidence: SquareConfidence | null;
  accountRisk: AccountRiskSnapshot | null;
}) {
  const api = safety?.api_key_check || {};
  const pending = safety?.pending_order;
  const missing = safety?.missing_protection_symbols || [];
  const failed = safety?.failed_protection_orders || [];
  const protectionOk = Boolean(safety?.protection_ok);
  const entryBlocked = entryConfirmation && entryConfirmation.passed === false;
  const accountBlocked = Boolean(accountRisk?.entry_blocked);
  const tone = pending || missing.length || failed.length || api.error || api.futures_error || entryBlocked || accountBlocked || (liveMode && api.spot_trading_allowed === false) || (liveMode && api.futures_account_accessible === false) ? "danger" : liveMode ? "warning" : "success";
  return (
    <section className={`safety-panel tone-${tone}`}>
      <div className="safety-head">
        <Shield size={18} />
        <div>
          <strong>实盘安全</strong>
          <span>{liveMode ? "现货实盘操作需要二次确认" : "当前为 dry-run，合约显示仅为模拟"}</span>
        </div>
      </div>
      <div className="safety-grid">
        <div>
          <span>未决订单</span>
          <strong>{pending ? `${pending.side || "--"} ${pending.symbol || "--"}` : "无"}</strong>
          <small>{pending?.client_order_id || "没有未恢复的 clientOrderId"}</small>
        </div>
        <div>
          <span>保护单</span>
          <strong>{protectionOk ? "正常" : "需要关注"}</strong>
          <small>
            {missing.length ? `缺失：${missing.join(", ")}` : failed.length ? `失败：${failed.map((item) => item.symbol).join(", ")}` : "已记录交易所或模拟保护单"}
          </small>
        </div>
        <div>
          <span>API Key</span>
          <strong>{api.api_key_loaded ? `已加载 ****${api.api_key_suffix || ""}` : "未加载"}</strong>
          <small>{api.error || api.futures_error || (api.futures_account_accessible === false ? "未检测到 Futures 权限" : api.spot_trading_allowed === false ? "未检测到 SPOT 权限" : "请人工确认关闭提现并开启 IP 白名单")}</small>
        </div>
        <div>
          <span>入场确认</span>
          <strong>{entryConfirmation ? (entryConfirmation.passed ? "通过" : "阻止") : "等待"}</strong>
          <small>
            {entryConfirmation?.reason || `Square 置信度 ${textValue(squareConfidence?.score) || "--"}`}
          </small>
        </div>
        <div>
          <span>账户风控</span>
          <strong>{accountRisk ? (accountRisk.entry_blocked ? "阻止" : "正常") : "等待"}</strong>
          <small>
            {accountRisk?.entry_blocked && accountRisk.reason ? accountRisk.reason : accountRiskCapacitySummary(accountRisk)}
          </small>
        </div>
      </div>
    </section>
  );
}

function DashboardSecurityPanel({ security }: { security: DashboardSecurity | null }) {
  const readOnly = Boolean(security?.read_only);
  const localOnly = security?.local_only_host !== false;
  const tokenEnabled = Boolean(security?.token_enabled);
  const tone = readOnly ? "warning" : localOnly && tokenEnabled ? "success" : localOnly ? "warning" : "danger";
  return (
    <section className={`safety-panel tone-${tone}`}>
      <div className="safety-head">
        <KeyRound size={18} />
        <div>
          <strong>控制台安全</strong>
          <span>{readOnly ? "只读模式会阻止 POST 控制操作" : "允许本地通过校验的控制请求"}</span>
        </div>
      </div>
      <div className="safety-grid">
        <div>
          <span>只读模式</span>
          <strong>{readOnly ? "已启用" : "未启用"}</strong>
          <small>{readOnly ? "POST 控制接口已阻止" : "控制接口通过配置校验后可执行"}</small>
        </div>
        <div>
          <span>控制台 Token</span>
          <strong>{tokenEnabled ? "已启用" : "未启用"}</strong>
          <small>{tokenEnabled ? "POST 请求需要 Token" : "本地默认，未设置 Token"}</small>
        </div>
        <div>
          <span>Host / Origin</span>
          <strong>{security?.host_origin_check_enabled ? "已检查" : "未知"}</strong>
          <small>{localOnly ? `绑定到 ${security?.bound_host || "localhost"}` : security?.warning || "检测到非本机绑定"}</small>
        </div>
      </div>
    </section>
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

function MarketCurve({ chart, loading, error, chartUrl }: { chart: MarketChart | null; loading: boolean; error: string; chartUrl: string }) {
  const closes = (chart?.points || []).map((item) => asNumber(item.close)).filter((item): item is number => item !== null && Number.isFinite(item));
  const paths = buildMarketCurvePaths(closes);
  const change = asNumber(chart?.change_percent);
  const tone = change !== null && change < 0 ? "negative" : "positive";
  const label = loading ? "加载行情..." : error ? "行情接口异常" : change !== null ? `${chart?.range || "24H"} ${signedPercent(change)}` : chart?.range || "24H";
  const titleText = error ? error : "在 TradingView 打开走势图";
  return (
    <a className={`market-curve tone-${tone}`} href={chartUrl} target="_blank" rel="noreferrer" aria-label={`${label}，在 TradingView 打开`} title={titleText}>
      <svg viewBox="0 0 760 150" preserveAspectRatio="none">
        <defs>
          <linearGradient id="curveFill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="currentColor" stopOpacity="0.2" />
            <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path className="curve-fill" d={paths.fill} />
        <path className="curve-line" d={paths.line} />
        <line x1="0" y1={paths.baseline} x2="760" y2={paths.baseline} />
      </svg>
      <span>{label}</span>
    </a>
  );
}

function buildMarketCurvePaths(values: number[]) {
  const fallback = [122, 120, 106, 111, 98, 91, 73, 94, 86, 91, 84, 80, 72, 79, 66, 70, 63, 74, 59, 51, 57, 43, 50, 36, 47, 41, 44, 26];
  const width = 760;
  const top = 18;
  const bottom = 130;
  const baseline = 122;
  const prices = values.length >= 2 ? values : fallback.map((value) => bottom - value + top);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const points = prices.map((value, index) => {
    const x = prices.length === 1 ? width : (index / (prices.length - 1)) * width;
    const y = bottom - ((value - min) / range) * (bottom - top);
    return `${roundPathNumber(x)} ${roundPathNumber(y)}`;
  });
  const line = points.map((point, index) => `${index === 0 ? "M" : "L"}${point}`).join(" ");
  return {
    line,
    fill: `${line} L${width} 150 L0 150 Z`,
    baseline,
  };
}

function roundPathNumber(value: number): string {
  return value.toFixed(2).replace(/\.?0+$/, "");
}

function normalizeSymbol(symbol?: Primitive): string {
  const clean = String(symbol || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
  if (!clean) {
    return "";
  }
  return clean.endsWith("USDT") ? clean : `${clean}USDT`;
}

function marketTypeLabel(value?: string): string {
  return value === "futures" ? "合约" : "现货";
}

function positionModeText(item: { dry_run?: boolean | Primitive; market_type?: string; position_mode?: string }): string {
  const isDryRun = Boolean(item.dry_run);
  const market = item.market_type === "futures" || item.position_mode === "contract-sim" || item.position_mode === "futures-live" ? "合约" : "现货";
  return `${market}${isDryRun ? "模拟" : "实盘"}`;
}

function durationText(value?: Primitive): string {
  const seconds = asNumber(value);
  if (seconds === null || seconds <= 0) {
    return "--";
  }
  if (seconds < 3600) {
    return `${trimNumber(seconds / 60, 0)} 分钟`;
  }
  if (seconds < 86400) {
    return `${trimNumber(seconds / 3600, 1)} 小时`;
  }
  return `${trimNumber(seconds / 86400, 1)} 天`;
}

function tradingViewChartUrl(symbol: string): string {
  const cleanSymbol = normalizeSymbol(symbol) || "BTCUSDT";
  const params = new URLSearchParams({ symbol: `BINANCE:${cleanSymbol}` });
  return `https://www.tradingview.com/chart/?${params.toString()}`;
}

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "muted",
  onClick,
}: {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  tone?: "success" | "warning" | "danger" | "muted";
  onClick?: () => void;
}) {
  const content = (
    <>
      <div className="metric-icon">
        <Icon size={18} />
      </div>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </>
  );
  if (onClick) {
    return (
      <button className={`metric-card metric-card-button tone-${tone}`} type="button" onClick={onClick}>
        {content}
      </button>
    );
  }
  return <article className={`metric-card tone-${tone}`}>{content}</article>;
}

type PositionView = {
  symbol: string;
  baseAsset: string;
  quantity?: Primitive;
  entryPrice?: Primitive;
  highestPrice?: Primitive;
  quoteSpent?: Primitive;
  openedAt?: string;
  snapshot?: PositionSnapshot;
  modeLabel?: string;
};

function formatAbsQty(value: Primitive): string {
  const parsed = asNumber(value);
  return parsed === null ? formatQty(value) : formatQty(Math.abs(parsed));
}

function formatOrderQuantity(value: Primitive, fraction: number): string {
  const parsed = asNumber(value);
  if (parsed === null) {
    return "";
  }
  const quantity = Math.abs(parsed) * fraction;
  return quantity.toFixed(8).replace(/\.?0+$/, "");
}

function isPositiveNumber(value: string): boolean {
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0;
}

function formatLeverageBadge(value: Primitive): string {
  const parsed = asNumber(value);
  return parsed === null ? "--x" : `${trimNumber(parsed, 2)}x`;
}

function positionRawSide(item: PositionView): string {
  const snapshot = item.snapshot as (PositionSnapshot & Record<string, unknown>) | undefined;
  return [
    snapshot?.side,
    snapshot?.position_side,
    snapshot?.positionSide,
    snapshot?.direction,
    item.modeLabel,
  ]
    .filter((value) => value !== null && value !== undefined)
    .map((value) => String(value).toLowerCase())
    .join(" ");
}

function positionSide(item: PositionView): { label: "多" | "空"; tone: "tone-long" | "tone-short" } {
  const rawSide = positionRawSide(item);
  const quantity = asNumber(item.quantity ?? item.snapshot?.quantity);
  if (rawSide.includes("short") || rawSide.includes("sell") || rawSide.includes("空") || (quantity !== null && quantity < 0)) {
    return { label: "空", tone: "tone-short" };
  }
  return { label: "多", tone: "tone-long" };
}

function PositionsSummaryCard({
  positions,
  expanded,
  onToggle,
  onOpenTab,
}: {
  positions: PositionView[];
  expanded: boolean;
  onToggle: () => void;
  onOpenTab: () => void;
}) {
  const visible = expanded ? positions : positions.slice(0, 3);
  return (
    <article
      className="metric-card positions-summary-card metric-card-button"
      role="button"
      tabIndex={0}
      onClick={(event) => {
        if ((event.target as HTMLElement).closest("button")) {
          return;
        }
        onOpenTab();
      }}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onOpenTab();
        }
      }}
    >
      <div className="metric-icon">
        <Wallet size={18} />
      </div>
      <div className="positions-summary-head">
        <span>当前仓位</span>
        <button type="button" onClick={onOpenTab}>查看完整</button>
      </div>
      {!positions.length ? (
        <>
          <strong>暂无持仓</strong>
          <p>执行一次或启动循环后，这里会显示开仓标的。</p>
        </>
      ) : (
        <>
          <strong>{positions.length} 个持仓</strong>
          <div className="position-summary-list">
            {visible.map((item) => (
              <PositionSummaryLine item={item} key={item.symbol} />
            ))}
          </div>
          {positions.length > 3 ? (
            <button className="link-button" type="button" onClick={onToggle}>
              {expanded ? "收起" : `展开 ${positions.length - 3} 个更多仓位`}
            </button>
          ) : null}
        </>
      )}
    </article>
  );
}

function PositionSummaryLine({ item }: { item: PositionView }) {
  const pnl = asNumber(item.snapshot?.unrealized_pnl);
  const side = positionSide(item);
  return (
    <div className="position-summary-line">
      <strong>{item.symbol}</strong>
      <span>
        <b className={`position-side-badge ${side.tone}`}>{side.label}</b>
        {item.modeLabel || "持仓"} · {formatAbsQty(item.quantity)} · {formatMoney(item.quoteSpent, item.snapshot?.quote_asset || "USDT")}
      </span>
      <em className={pnl !== null && pnl < 0 ? "negative" : "positive"}>{signedMoney(item.snapshot?.unrealized_pnl, item.snapshot?.quote_asset || "USDT")}</em>
    </div>
  );
}

function PositionsPanel({
  positions,
  snapshots,
  onClosePosition,
  busySymbol,
  readOnly,
  readOnlyReason,
}: {
  positions: PositionView[];
  snapshots: PositionSnapshot[];
  onClosePosition: (symbol: string, quantity: string) => void;
  busySymbol: string;
  readOnly: boolean;
  readOnlyReason: string;
}) {
  if (!positions.length) {
    return <EmptyState title="暂无当前仓位" text="开仓后这里会显示完整仓位、浮动盈亏和价格线。" />;
  }
  return (
    <div className="positions-panel">
      <section className="positions-list-panel">
        <div className="section-heading compact">
          <div>
            <p className="eyebrow">Positions</p>
            <h2>完整持仓</h2>
            <span>显示全部当前开仓、数量、成本、现价和浮动盈亏</span>
          </div>
        </div>
        <div className="position-card-grid">
          {positions.map((item) => (
            <PositionDetailCard
              item={item}
              key={`${item.symbol}-${formatOrderQuantity(item.quantity, 1)}`}
              onClosePosition={onClosePosition}
              busy={busySymbol === item.symbol}
              readOnly={readOnly}
              readOnlyReason={readOnlyReason}
            />
          ))}
        </div>
      </section>
      <PositionPriceCharts snapshots={snapshots} />
    </div>
  );
}

function PositionDetailCard({
  item,
  onClosePosition,
  busy,
  readOnly,
  readOnlyReason,
}: {
  item: PositionView;
  onClosePosition: (symbol: string, quantity: string) => void;
  busy: boolean;
  readOnly: boolean;
  readOnlyReason: string;
}) {
  const pnl = asNumber(item.snapshot?.unrealized_pnl);
  const side = positionSide(item);
  const quoteAsset = item.snapshot?.quote_asset || "USDT";
  const isContractSim = Boolean(item.snapshot?.contract_simulation);
  const stopGuardWarning = textValue(item.snapshot?.stop_guard_warning);
  const configuredStopValue = `${formatPercent(item.snapshot?.configured_stop_loss_pct ?? item.snapshot?.initial_stop_loss_pct ?? 0)} / ${formatPrice(item.snapshot?.configured_stop_price)}`;
  const bufferValue = `${formatPercent(item.snapshot?.liquidation_stop_buffer_pct ?? 0)} 缓冲`;
  const [closeQuantity, setCloseQuantity] = useState(() => formatOrderQuantity(item.quantity, 1));
  const closeOptions = [25, 50, 75, 100];
  return (
    <article className="position-detail-card">
      <div className="position-detail-head">
        <div className="position-title">
          <div>
            <a className="position-symbol-link" href={tradingViewChartUrl(item.symbol)} target="_blank" rel="noreferrer" title="在 TradingView 打开">
              <strong>{item.symbol}</strong>
              <ExternalLink size={15} aria-hidden="true" />
            </a>
            <b className={`position-side-badge ${side.tone}`}>{side.label}</b>
            <b className="position-leverage-badge">{formatLeverageBadge(item.snapshot?.leverage_multiplier)}</b>
          </div>
          <span>{item.modeLabel || "持仓"}</span>
        </div>
        <div className="position-pnl-stack">
          <em className={pnl !== null && pnl < 0 ? "negative" : "positive"}>{signedMoney(item.snapshot?.unrealized_pnl, quoteAsset)}</em>
          <span className={pnl !== null && pnl < 0 ? "negative" : "positive"}>{signedPercent(item.snapshot?.unrealized_pnl_pct)}</span>
        </div>
      </div>
      <div className="position-detail-grid">
        <PositionFact label="仓位数量" value={formatAbsQty(item.quantity)} />
        <PositionFact label="开仓均价" value={formatPrice(item.entryPrice)} />
        <PositionFact label="现价" value={formatPrice(item.snapshot?.current_price)} />
        <PositionFact label={isContractSim ? "名义仓位" : "最高"} value={isContractSim ? formatMoney(item.snapshot?.notional_quote, quoteAsset) : formatPrice(item.highestPrice)} />
        <PositionFact label="有效止损" value={formatPrice(item.snapshot?.dynamic_stop_price)} tone={item.snapshot?.stop_triggered ? "danger" : undefined} />
        <PositionFact label={isContractSim ? "预估强平" : "止盈价"} value={isContractSim ? formatPrice(item.snapshot?.liquidation_price) : formatPrice(item.snapshot?.take_profit_price)} tone={item.snapshot?.liquidation_triggered ? "danger" : item.snapshot?.take_profit_triggered ? "success" : undefined} />
        <PositionFact label={isContractSim ? "保证金" : "投入金额"} value={formatMoney(isContractSim ? item.snapshot?.margin_quote : item.quoteSpent, quoteAsset)} />
        <PositionFact label="开仓时间" value={item.openedAt ? formatTime(item.openedAt) : "--"} />
        {isContractSim ? <PositionFact label="配置止损" value={configuredStopValue} tone={item.snapshot?.stop_guard_tightened ? "danger" : undefined} /> : null}
        {isContractSim ? <PositionFact label="距强平" value={formatPercent(item.snapshot?.liquidation_distance_pct)} tone={item.snapshot?.liquidation_triggered ? "danger" : undefined} /> : null}
        {isContractSim ? <PositionFact label="强平保护" value={bufferValue} tone={item.snapshot?.stop_guard_tightened ? "danger" : undefined} /> : null}
      </div>
      {isContractSim && stopGuardWarning ? <p className="position-risk-warning">{stopGuardWarning}</p> : null}
      <div className="position-close-panel">
        <span>平仓</span>
        <div className="position-close-options">
          {closeOptions.map((percent) => (
            <button
              type="button"
              key={percent}
              disabled={busy || readOnly}
              title={readOnlyReason}
              onClick={() => onClosePosition(item.symbol, formatOrderQuantity(item.quantity, percent / 100))}
            >
              {percent}%
            </button>
          ))}
        </div>
        <div className="position-close-custom">
          <input
            value={closeQuantity}
            inputMode="decimal"
            onChange={(event) => setCloseQuantity(event.target.value)}
            aria-label={`${item.symbol} 平仓数量`}
          />
          <button type="button" disabled={busy || readOnly || !isPositiveNumber(closeQuantity)} title={readOnlyReason} onClick={() => onClosePosition(item.symbol, closeQuantity)}>
            {busy ? "处理中" : "平仓"}
          </button>
        </div>
      </div>
    </article>
  );
}

function PositionFact({ label, value, tone }: { label: string; value: string; tone?: "success" | "danger" }) {
  return (
    <div className={`position-fact${tone ? ` ${tone}` : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PositionPriceCharts({ snapshots }: { snapshots: PositionSnapshot[] }) {
  const active = snapshots.filter((item) => item?.symbol);
  if (!active.length) {
    return null;
  }
  return (
    <section className="position-chart-panel" aria-label="持仓价格线">
      <div className="section-heading compact">
        <p className="eyebrow">Position Map</p>
        <h2>持仓价格线</h2>
        <span>入场价、现价、止盈价和有效止损价</span>
      </div>
      <div className="position-chart-list">
        {active.map((snapshot, index) => (
          <PositionPriceChart snapshot={snapshot} key={`${snapshot.symbol || "position"}-${index}`} />
        ))}
      </div>
    </section>
  );
}

function PositionPriceChart({ snapshot }: { snapshot: PositionSnapshot }) {
  const points = [
    { key: "stop", label: "有效止损", value: asNumber(snapshot.dynamic_stop_price), text: formatPrice(snapshot.dynamic_stop_price) },
    { key: "entry", label: "入场", value: asNumber(snapshot.entry_price), text: formatPrice(snapshot.entry_price) },
    { key: "current", label: "现价", value: asNumber(snapshot.current_price), text: formatPrice(snapshot.current_price) },
    { key: "take", label: "止盈", value: asNumber(snapshot.take_profit_price), text: formatPrice(snapshot.take_profit_price) },
  ].filter((item): item is { key: string; label: string; value: number; text: string } => item.value !== null && Number.isFinite(item.value) && item.value > 0);

  if (points.length < 2) {
    return (
      <article className="position-chart-row">
        <div className="price-chart-head">
          <strong>{snapshot.symbol || "--"}</strong>
          <span>{snapshot.price_error || "等待价格数据"}</span>
        </div>
      </article>
    );
  }

  const rawMin = Math.min(...points.map((item) => item.value));
  const rawMax = Math.max(...points.map((item) => item.value));
  const padding = Math.max((rawMax - rawMin) * 0.08, rawMax * 0.002);
  const min = rawMin - padding;
  const max = rawMax + padding;
  const range = max - min || 1;

  return (
    <article className="position-chart-row">
      <div className="price-chart-head">
        <strong>{snapshot.symbol || "--"}</strong>
        <span>
          {snapshot.mode_label || "持仓"} · {formatQty(snapshot.quantity)} · {formatMoney(snapshot.quote_spent, snapshot.quote_asset || "")}
        </span>
      </div>
      <div className="price-line">
        {points.map((item) => {
          const left = Math.min(100, Math.max(0, ((item.value - min) / range) * 100));
          return (
            <span
              className={`price-marker marker-${item.key}`}
              data-label={item.label}
              key={item.key}
              style={{ left: `${left}%` } as CSSProperties}
              title={`${item.label} ${item.text}`}
            />
          );
        })}
      </div>
      <div className="price-chart-legend">
        {points.map((item) => (
          <span className={`legend-item marker-${item.key}`} key={item.key}>
            {item.label} <strong>{item.text}</strong>
          </span>
        ))}
      </div>
    </article>
  );
}

function ActionButton({
  icon: Icon,
  label,
  onClick,
  busy,
  disabled = false,
  title = "",
  tone = "secondary",
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
  title?: string;
  tone?: "primary" | "secondary" | "danger";
}) {
  return (
    <button className={`action-button tone-${tone}`} type="button" disabled={busy || disabled} title={title} onClick={onClick}>
      <Icon size={16} />
      <span>{busy ? "处理中" : label}</span>
    </button>
  );
}

function HotAssetsTable({
  items,
  favoriteSymbols,
  onToggleFavorite,
}: {
  items: HotAsset[];
  favoriteSymbols: Set<string>;
  onToggleFavorite: (symbol: string) => void;
}) {
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
              <th>市场</th>
              <th>综合分</th>
              <th>市场分</th>
              <th>广场分</th>
              <th>实时价格</th>
            <th>24h 涨幅</th>
            <th>波动率</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => (
            <tr key={`${item.symbol || item.asset || index}-${index}`} className={index === 0 ? "is-leader" : ""}>
              <td className="mono muted">{index + 1}</td>
              <td className="favorite-cell">
                <FavoriteButton
                  symbol={item.symbol || item.asset || ""}
                  active={favoriteSymbols.has(normalizeSymbol(item.symbol || item.asset))}
                  onToggle={onToggleFavorite}
                />
              </td>
              <td className="symbol-cell">
                <a href={tradingViewChartUrl(item.symbol || item.asset || "")} target="_blank" rel="noreferrer">
                  <span className="coin-avatar">{coinInitial(item.symbol || item.asset)}</span>
                  {item.symbol || item.asset || "--"}
                </a>
              </td>
              <td>{marketTypeLabel(item.market_type)}</td>
              <td className="mono accent">{formatScore(item.score)}</td>
              <td className="mono">{formatScore(item.market_score)}</td>
              <td className="mono">
                {formatScore(item.square_score)}
                {item.mentions ? <span className="muted"> ({item.mentions})</span> : null}
              </td>
              <td className="mono">{formatPrice(item.last_price)}</td>
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

function FavoriteButton({
  symbol,
  active,
  onToggle,
}: {
  symbol: string;
  active: boolean;
  onToggle: (symbol: string) => void;
}) {
  return (
    <button
      className={`favorite-button ${active ? "is-active" : ""}`}
      type="button"
      disabled={!normalizeSymbol(symbol)}
      title={active ? "取消收藏" : "收藏币种"}
      onClick={() => onToggle(symbol)}
    >
      <Star size={16} fill="currentColor" />
    </button>
  );
}

function FavoritesPanel({
  favoriteSymbols,
  hotAssets,
  onToggleFavorite,
}: {
  favoriteSymbols: string[];
  hotAssets: HotAsset[];
  onToggleFavorite: (symbol: string) => void;
}) {
  if (!favoriteSymbols.length) {
    return <EmptyState title="暂无收藏币种" text="点击市场指令或热门币种列表里的星标，把关注的币种加入收藏。" />;
  }
  const hotAssetBySymbol = new Map(hotAssets.map((item) => [normalizeSymbol(item.symbol || item.asset), item]));
  const favoriteRows = favoriteSymbols.map((symbol) => hotAssetBySymbol.get(symbol) || { symbol });
  return (
    <div className="favorites-panel">
      <div className="section-heading compact">
        <div>
          <p className="eyebrow">Favorites</p>
          <h2>收藏币种</h2>
          <span>收藏会保存在本地浏览器，币种名称点击打开 TradingView。</span>
        </div>
      </div>
      <HotAssetsTable items={favoriteRows} favoriteSymbols={new Set(favoriteSymbols)} onToggleFavorite={onToggleFavorite} />
    </div>
  );
}

function StrategyPanel({
  activePreset,
  applyStrategyPreset,
  openSettingsTab,
  settings,
}: {
  activePreset: StrategyPresetKey;
  applyStrategyPreset: (name: StrategyPresetKey) => void;
  openSettingsTab: (tab: SettingsTabKey) => void;
  settings: SettingsState;
}) {
  const strategyLinks: Array<{ title: string; detail: string; tab: SettingsTabKey }> = [
    { title: "信号筛选", detail: "涨幅、波动率、成交额、热门帖子数和热门币种数。", tab: "signal" },
    { title: "交易范围", detail: "白名单、黑名单、大盘过滤和交易资产范围。", tab: "scope" },
    { title: "风控退出", detail: "止损、止盈、保本、移动止损和日内风控。", tab: "risk" },
    { title: "运行模式", detail: "循环秒数、测试网、实盘和浏览器抓广场。", tab: "runtime" },
  ];
  const simulatedEquity = asNumber(settings.dry_run_initial_equity_usdt);
  const leverage = asNumber(settings.leverage_multiplier);
  const maxDryRunNotional =
    settings.contract_simulation_enabled && simulatedEquity !== null && leverage !== null
      ? simulatedEquity * leverage
      : null;
  return (
    <div className="strategy-panel">
      <section className="strategy-hero">
        <div>
          <p className="eyebrow">Strategy</p>
          <h2>策略中枢</h2>
          <span>策略相关入口单独收纳，后续可以继续扩展独立策略、回测和参数模板。</span>
        </div>
        <strong>{PRESET_LABELS[activePreset]}</strong>
      </section>
      <section className="strategy-preset-panel">
        <span>策略参数预设</span>
        <div className="preset-row">
          {(Object.keys(STRATEGY_PRESETS) as StrategyPresetKey[]).map((name) => (
            <button
              className={activePreset === name ? "is-active" : ""}
              type="button"
              key={name}
              onClick={() => applyStrategyPreset(name)}
            >
              {PRESET_LABELS[name]}
            </button>
          ))}
        </div>
        <small>
          {settings.contract_simulation_enabled ? `合约模拟 ${settings.leverage_multiplier}x` : "现货模拟"}
          {" · "}最大持仓 {settings.max_open_positions}
          {" · "}单笔保证金 {settings.order_quote_amount} {settings.quote_asset}
          {maxDryRunNotional !== null ? ` · 最大名义 ${formatMoney(maxDryRunNotional, settings.quote_asset)}` : ""}
          {" · "}固定止盈 {Number(settings.take_profit_pct) > 0 ? `${settings.take_profit_pct}%` : "关闭"}
        </small>
      </section>
      <section className="strategy-link-grid">
        {strategyLinks.map((item) => (
          <button type="button" key={item.tab} onClick={() => openSettingsTab(item.tab)}>
            <strong>{item.title}</strong>
            <span>{item.detail}</span>
          </button>
        ))}
      </section>
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

function TradesPanel({
  stats,
  fallbackTrades,
  page,
  view,
  offset,
  loading,
  error,
  onViewChange,
  onPageChange,
  onRefresh,
}: {
  stats: PerformanceStats | null | undefined;
  fallbackTrades: TradeItem[];
  page: TradeJournalPage | null;
  view: "round_trips" | "events";
  offset: number;
  loading: boolean;
  error: string;
  onViewChange: (view: "round_trips" | "events") => void;
  onPageChange: (offset: number) => void;
  onRefresh: () => void;
}) {
  const quote = stats?.quote_asset || "USDT";
  const tradeCount = stats?.trade_count ?? stats?.completed_trades ?? 0;
  const eventCount = stats?.event_count ?? page?.total ?? fallbackTrades.length;
  const items = page?.items || [];
  const total = Number(page?.total ?? 0);
  const limit = Number(page?.limit ?? 25) || 25;
  const canPrev = offset > 0;
  const canNext = offset + limit < total;
  const fallbackRecent = fallbackTrades.slice(-10).reverse();
  return (
    <div className="stack-panel">
      <div className="stats-grid">
        <StatTile label="完整交易" value={trimNumber(tradeCount, 0)} detail={`动作流水 ${trimNumber(eventCount, 0)}`} />
        <StatTile label="胜率" value={formatPercent(stats?.win_rate ?? 0)} detail={`盈亏比 ${stats?.profit_factor == null ? "--" : trimNumber(stats.profit_factor, 2, 2)}`} />
        <StatTile label="总盈亏" value={signedMoney(stats?.total_pnl ?? 0, quote)} detail={`最大回撤 ${formatMoney(stats?.max_drawdown ?? 0, quote)}`} tone={Number(stats?.total_pnl || 0) < 0 ? "danger" : "success"} />
        <StatTile label="平均盈亏" value={signedMoney(stats?.avg_pnl ?? 0, quote)} detail={`平均收益 ${signedPercent(stats?.avg_return_pct ?? 0)}`} tone={Number(stats?.avg_pnl || 0) < 0 ? "danger" : "success"} />
        <StatTile label="最佳交易" value={signedMoney(stats?.best_trade ?? 0, quote)} detail="单笔最大盈利" tone="success" />
        <StatTile label="最差交易" value={signedMoney(stats?.worst_trade ?? 0, quote)} detail="单笔最大亏损" tone="danger" />
        <StatTile label="毛利润" value={formatMoney(stats?.gross_profit ?? 0, quote)} detail={`毛亏损 ${formatMoney(stats?.gross_loss ?? 0, quote)}`} tone="success" />
        <StatTile label="当前连续" value={trimNumber(stats?.current_streak ?? 0, 0)} detail={streakLabel(stats?.current_streak_type)} />
      </div>
      <div className="trade-toolbar">
        <div className="segmented-control" aria-label="交易记录视图">
          <button type="button" className={view === "round_trips" ? "is-active" : ""} onClick={() => onViewChange("round_trips")}>
            完整交易
          </button>
          <button type="button" className={view === "events" ? "is-active" : ""} onClick={() => onViewChange("events")}>
            动作流水
          </button>
        </div>
        <div className="trade-pager">
          <button className="action-button icon-only" type="button" disabled={loading} onClick={onRefresh} title="刷新交易记录">
            <RefreshCw size={16} />
          </button>
          <button className="action-button" type="button" disabled={!canPrev || loading} onClick={() => onPageChange(Math.max(0, offset - limit))}>
            上一页
          </button>
          <span>
            {total ? `${offset + 1}-${Math.min(offset + limit, total)} / ${total}` : "0 / 0"}
          </span>
          <button className="action-button" type="button" disabled={!canNext || loading} onClick={() => onPageChange(offset + limit)}>
            下一页
          </button>
        </div>
      </div>
      {error ? <p className="negative">交易记录加载失败：{error}</p> : null}
      {loading && !items.length ? (
        <EmptyState title="正在加载交易记录" text="从本地复盘数据库读取分页数据。" />
      ) : view === "round_trips" && items.length ? (
        <TradeRoundTripTable items={items as TradeRoundTrip[]} quote={quote} />
      ) : view === "events" && items.length ? (
        <TradeEventTable items={items as TradeItem[]} quote={quote} />
      ) : fallbackRecent.length ? (
        <TradeEventTable items={fallbackRecent} quote={quote} />
      ) : (
        <EmptyState title="暂无交易记录" text="模拟或实盘成交后，这里会显示完整交易和动作流水。" />
      )}
    </div>
  );
}

function TradeRoundTripTable({ items, quote }: { items: TradeRoundTrip[]; quote: string }) {
  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            <th>平仓时间</th>
            <th>模式</th>
            <th>标的</th>
            <th>数量</th>
            <th>入场价</th>
            <th>平仓价</th>
            <th>盈亏</th>
            <th>收益率</th>
            <th>退出原因</th>
            <th>持仓时长</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, index) => {
            const pnl = asNumber(item.pnl) || 0;
            return (
              <tr key={`${item.id || item.exit_time || "round"}-${index}`}>
                <td>{formatTime(item.exit_time)}</td>
                <td><span className={`pill ${Boolean(item.dry_run) ? "tone-warning" : "tone-danger"}`}>{positionModeText(item)}</span></td>
                <td className="symbol-cell">{item.symbol || "--"}</td>
                <td className="mono">{formatQty(item.quantity)}</td>
                <td className="mono">{formatPrice(item.entry_price)}</td>
                <td className="mono">{formatPrice(item.exit_price)}</td>
                <td className={pnl < 0 ? "negative mono" : "positive mono"}>{signedMoney(item.pnl, quote)}</td>
                <td className={pnl < 0 ? "negative mono" : "positive mono"}>{signedPercent(item.return_pct)}</td>
                <td><span className="pill tone-danger">{actionLabel(item.exit_reason)}</span></td>
                <td>{durationText(item.duration_seconds)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function TradeEventTable({ items, quote }: { items: TradeItem[]; quote: string }) {
  return (
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
          {items.map((item, index) => {
            const action = item.action || "";
            const isBuy = action.includes("BUY");
            const isDryRun = Boolean(item.dry_run);
            return (
              <tr key={`${item.id || item.ts || "trade"}-${index}`}>
                <td>{formatTime(item.ts)}</td>
                <td>
                  <span className={`pill ${isDryRun ? "tone-warning" : "tone-danger"}`}>{positionModeText(item)}</span>
                </td>
                <td>
                  <span className={`pill ${isBuy ? "tone-success" : "tone-danger"}`}>{actionLabel(action)}</span>
                </td>
                <td className="symbol-cell">{item.symbol || "--"}</td>
                <td className="mono">{formatQty(item.quantity)}</td>
                <td className="mono">{formatPrice(item.price)}</td>
                <td className="mono">{formatMoney(item.fee_amount, item.fee_asset || quote)}</td>
                <td className="mono">{formatMoney(tradeAmount(item), quote)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
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

function DiagnosticsPanel({
  diagnostics,
  settings,
  busy,
  readOnly,
  readOnlyReason,
  updateSetting,
  onDiagnose,
  onUpdateReturns,
}: {
  diagnostics: Diagnostics | null;
  settings: SettingsState;
  busy: boolean;
  readOnly: boolean;
  readOnlyReason: string;
  updateSetting: <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => void;
  onDiagnose: () => void;
  onUpdateReturns: () => void;
}) {
  const urls = diagnostics?.urls || [];
  const posts = diagnostics?.display_posts || [];
  const signalStats = diagnostics?.signal_record_stats;
  const checkedAt = diagnostics?.checked_at ? formatTime(diagnostics.checked_at) : "--";
  return (
    <div className="diagnostics-layout">
      <div className="diagnostic-toolbar">
        <label className="toggle-row">
          <input
            type="checkbox"
            checked={Boolean(settings.square_browser_mode)}
            onChange={(event) => updateSetting("square_browser_mode", event.target.checked)}
          />
          <span>浏览器抓广场</span>
        </label>
        <div className="segmented-control" aria-label="真实帖子展示数量">
          {["10", "20"].map((value) => (
            <button
              key={value}
              className={settings.square_diagnostic_limit === value ? "is-active" : ""}
              type="button"
              onClick={() => updateSetting("square_diagnostic_limit", value)}
            >
              {value} 条
            </button>
          ))}
        </div>
        <button className="action-button" type="button" disabled={busy || readOnly} title={readOnlyReason} onClick={onDiagnose}>
          <Search size={16} />
          <span>{busy ? "诊断中" : "重新诊断"}</span>
        </button>
        <button className="action-button" type="button" disabled={busy || readOnly} title={readOnlyReason} onClick={onUpdateReturns}>
          <Database size={16} />
          <span>Update returns</span>
        </button>
      </div>
      {!diagnostics ? <EmptyState title="暂无诊断结果" text="点击诊断广场后查看 Binance Square 抓取与解析状态。" /> : null}
      {diagnostics ? (
        <>
      <div className="diagnostic-summary">
        <span>抓取反馈</span>
        <strong>{diagnostics.mode || "--"} · {checkedAt}</strong>
        <p>
          有效帖子 {diagnostics.total_posts ?? 0}
          {diagnostics.raw_posts !== undefined ? ` · 原始 ${diagnostics.raw_posts}` : ""}
          {diagnostics.filtered_out_posts !== undefined ? ` · 过滤 ${diagnostics.filtered_out_posts}` : ""}
          {diagnostics.browser_posts_raw !== undefined ? ` · 浏览器 ${diagnostics.browser_posts_raw}` : ""}
          {diagnostics.display_limit !== undefined ? ` · 展示 ${diagnostics.displayed_posts ?? 0}/${diagnostics.display_limit}` : ""}
        </p>
        <p>
          模式 {diagnostics.extractor_mode || "--"}
          {diagnostics.square_fetch_latency_ms !== undefined ? ` · 耗时 ${trimNumber(diagnostics.square_fetch_latency_ms, 0)}ms` : ""}
          {diagnostics.api_response_count !== undefined ? ` · API 响应 ${diagnostics.api_response_count}` : ""}
          {diagnostics.api_post_count !== undefined ? ` · API 帖子 ${diagnostics.api_post_count}` : ""}
          {diagnostics.json_post_count !== undefined ? ` · JSON ${diagnostics.json_post_count}` : ""}
          {diagnostics.html_post_count !== undefined ? ` · HTML ${diagnostics.html_post_count}` : ""}
          {diagnostics.rendered_text_post_count !== undefined ? ` · 文本 ${diagnostics.rendered_text_post_count}` : ""}
        </p>
        <p>
          新帖 {diagnostics.new_post_count ?? 0}
          {diagnostics.duplicate_post_count !== undefined ? ` · 重复 ${diagnostics.duplicate_post_count}` : ""}
          {diagnostics.latest_post_time ? ` · 最新 ${formatTime(diagnostics.latest_post_time)}` : ""}
          {diagnostics.consecutive_failures !== undefined ? ` · 连续失败 ${diagnostics.consecutive_failures}` : ""}
        </p>
        <p>帖子分 = 币种符号分 + 交易语境分 + 非看空/做空语境分 + 流量/长度分 + 时间衰减分。有效帖子仍按原策略过滤；下方列表展示真实抓到的原始帖子。</p>
        {diagnostics.browser_error ? <p className="negative">浏览器错误：{diagnostics.browser_error}</p> : null}
        {diagnostics.hint ? <p className="warning">{diagnostics.hint}</p> : null}
        {signalStats ? (
          <p>
            Signal records {signalStats.record_count ?? 0}
            {signalStats.last_record_at ? ` · latest ${formatTime(signalStats.last_record_at)}` : ""}
            {signalStats.entered_count !== undefined ? ` · entered ${signalStats.entered_count}` : ""}
            {signalStats.skipped_count !== undefined ? ` · skipped ${signalStats.skipped_count}` : ""}
            {signalStats.future_returns_count !== undefined ? ` · returns ${signalStats.future_returns_count}` : ""}
            {signalStats.updated_count !== undefined ? ` · updated ${signalStats.updated_count}` : ""}
          </p>
        ) : null}
        {signalStats?.decision_groups ? (
          <p>
            Groups{" "}
            {Object.entries(signalStats.decision_groups)
              .map(([key, value]) => `${key}:${value}`)
              .join(" · ")}
          </p>
        ) : null}
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
      {posts.length ? <DiagnosticPostList posts={posts} /> : <EmptyState title="没有真实帖子" text="当前诊断没有解析到可展示的 Binance Square 帖子内容。" />}
        </>
      ) : null}
    </div>
  );
}

function DiagnosticPostList({ posts }: { posts: DiagnosticsPost[] }) {
  return (
    <div className="diagnostic-post-list">
      {posts.map((post, index) => (
        <article className="diagnostic-post" key={`${post.url || post.text || "post"}-${index}`}>
          <div className="diagnostic-post-head">
            <div>
              <span className={post.valid_trading_post ? "post-status positive" : "post-status warning"}>
                {post.valid_trading_post ? "有效帖" : "原始帖"}
              </span>
              <strong>{post.title || `真实帖子 #${index + 1}`}</strong>
            </div>
            <span className="score-badge">分数 {formatScore(post.score)}</span>
          </div>
          <p>{post.text || "--"}</p>
          <div className="post-meta-row">
            {post.extractor_mode ? <span>来源 {post.extractor_mode}</span> : null}
            {post.author ? <span>作者 {post.author}</span> : null}
            {post.created_at ? <span>时间 {formatTime(post.created_at)}</span> : null}
            <span>流量 {trimNumber(post.traffic_score, 0)}</span>
            <span>长度 {trimNumber(post.score_basis?.text_length, 0)}</span>
            {post.post_id ? <span>ID {post.post_id}</span> : null}
            {post.url ? <span className="url-cell">{post.url}</span> : null}
          </div>
          <div className="symbol-chip-row">
            {post.symbols?.length ? (
              post.symbols.map((item) => (
                <span className="symbol-chip" key={`${item.asset}-${item.mentions}`}>
                  {item.asset} × {item.mentions ?? 0}
                </span>
              ))
            ) : (
              <span className="muted">未识别币种符号</span>
            )}
          </div>
          <div className="score-breakdown">
            <span>符号 {formatScore(post.score_basis?.symbol_score)}</span>
            <span>交易语境 {formatScore(post.score_basis?.context_score)}</span>
            <span>非看空/做空 {formatScore(post.score_basis?.long_context_score)}</span>
            <span>流量 {formatScore(post.score_basis?.traffic_score)}</span>
            <span>长度 {formatScore(post.score_basis?.length_score)}</span>
            <span>时间 {formatScore(post.score_basis?.time_decay_score)}</span>
          </div>
          <div className="reason-row">
            {(post.filter_reasons || []).map((reason) => (
              <span key={reason}>{reason}</span>
            ))}
          </div>
        </article>
      ))}
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

function NotificationPanel({
  settings,
  updateSetting,
}: {
  settings: SettingsState;
  updateSetting: <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => void;
}) {
  const [telegramTestBusy, setTelegramTestBusy] = useState(false);
  const [telegramTestResult, setTelegramTestResult] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const fieldProps = { settings, updateSetting, setFixedStopEdited: (_value: boolean) => undefined };
  const toggleProps = { settings, updateSetting };

  function saveCurrentSettings() {
    saveSettings(settings);
    setSaveMessage("已保存");
    window.setTimeout(() => setSaveMessage(""), 1800);
  }

  async function testTelegram() {
    setTelegramTestBusy(true);
    setTelegramTestResult("");
    try {
      const response = await fetch("/api/test-telegram", {
        method: "POST",
        headers: dashboardHeaders(settings),
        body: JSON.stringify(settings),
      });
      const data = (await response.json()) as { ok?: boolean; error?: string };
      if (!response.ok || !data.ok) {
        throw new Error(data.error || response.statusText);
      }
      setTelegramTestResult("测试通知已发送");
    } catch (error) {
      setTelegramTestResult(error instanceof Error ? error.message : "测试通知失败");
    } finally {
      setTelegramTestBusy(false);
    }
  }

  return (
    <div className="notification-panel">
      <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="Telegram 通知" description="交易事件和异常通过 Telegram Bot 推送。">
        <div className="toggle-grid">
          <SettingsToggle {...toggleProps} name="telegram_enabled" label="启用 Telegram 通知" />
        </div>
        <SettingsField {...fieldProps} name="telegram_bot_token" label="Bot Token" type="password" placeholder="123456:ABC-DEF..." help="从 @BotFather 获取的 Bot Token；页面不会从后端回显 Token。" full />
        <SettingsField {...fieldProps} name="telegram_chat_id" label="Chat ID" placeholder="-100123456789" help="目标聊天 ID，可以是个人或群组。" />
        <SettingsField {...fieldProps} name="dashboard_auth_token" label="Dashboard Token" type="password" help="Set this to the same value as DASHBOARD_AUTH_TOKEN; it is stored only in this browser." full />
        <div className="telegram-test-row">
          <button className="action-button tone-secondary" type="button" disabled={telegramTestBusy} onClick={testTelegram}>
            <Send size={16} />
            <span>{telegramTestBusy ? "发送中" : "测试通知"}</span>
          </button>
          {telegramTestResult ? <small>{telegramTestResult}</small> : null}
        </div>
      </SettingsSection>
    </div>
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
  const [saveMessage, setSaveMessage] = useState("");
  const fieldProps = { settings, updateSetting, setFixedStopEdited };
  const toggleProps = { settings, updateSetting };

  function saveCurrentSettings() {
    saveSettings(settings);
    setSaveMessage("已保存");
    window.setTimeout(() => setSaveMessage(""), 1800);
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
          <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="基础交易" description="控制交易计价、单笔投入和本地状态文件。">
            <SettingsField {...fieldProps} name="quote_asset" label="计价币种" />
            <SettingsField {...fieldProps} name="trade_market_mode" label="交易市场模式" help="futures_preferred=合约优先；futures_only=仅合约；spot_only=仅现货。" />
            <SettingsField {...fieldProps} name="futures_margin_type" label="合约保证金模式" help="默认 ISOLATED 逐仓；也可填 CROSSED 全仓。" />
            <SettingsField {...fieldProps} name="order_quote_amount" label="单笔金额" type="number" min="1" step="1" />
            <SettingsField {...fieldProps} name="dry_run_initial_equity_usdt" label="模拟初始资金 USDT" type="number" min="1" step="1" help="仅用于 dry-run 合约模拟的保证金池；最大名义仓位 = 模拟初始资金 * 杠杆。留空时按单笔金额 * 最大持仓数估算。" />
            <SettingsField {...fieldProps} name="max_open_positions" label="最大持仓数" type="number" min="1" step="1" help="允许同时持有的仓位数量；保守默认为 1，标准预设为 15，激进预设为 20。" />
            <SettingsField {...fieldProps} name="leverage_multiplier" label="杠杆倍数" type="number" min="0.1" step="0.1" help="合约优先模式下用于 USDT-M 合约实盘和 dry-run 合约模拟；现货兜底时不使用杠杆。" />
            <SettingsField {...fieldProps} name="state_file" label="状态文件" full />
            <div className="toggle-grid is-full">
              <SettingsToggle {...toggleProps} name="contract_simulation_enabled" label="Dry-run 使用合约模拟" />
            </div>
          </SettingsSection>
        )}
        {activeTab === "signal" && (
          <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="信号筛选" description="候选进入排序前必须满足的行情和广场热度条件。">
            <SettingsField {...fieldProps} name="min_price_change_percent" label="最低涨幅 %" type="number" step="0.1" />
            <SettingsField {...fieldProps} name="min_volatility_percent" label="最低波动 %" type="number" step="0.1" />
            <SettingsField {...fieldProps} name="min_quote_volume" label="最低成交额" type="number" min="0" step="100000" full />
            <SettingsField {...fieldProps} name="top_post_limit" label="热门帖子数" type="number" min="1" step="1" />
            <SettingsField {...fieldProps} name="top_coin_limit" label="热门币种数" type="number" min="1" step="1" />
            <SettingsField {...fieldProps} name="min_square_confidence_score" label="Square 最低置信度" type="number" min="0" max="100" step="1" help="低于该分数时跳过自动入场，避免数据源失效后退化成纯追涨。" />
            <div className="toggle-grid is-full">
              <SettingsToggle {...toggleProps} name="kline_confirmation_enabled" label="启用短周期 K 线确认" />
            </div>
          </SettingsSection>
        )}
        {activeTab === "scope" && (
          <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="交易范围" description="控制允许交易的币种、大盘环境过滤和实盘账户同步。">
            <SettingsField {...fieldProps} name="asset_whitelist" label="白名单" placeholder="BTC,ETH,SOL 或 SOLUSDT" help="填写后只交易这些币种；留空表示不限制。" full />
            <SettingsField {...fieldProps} name="asset_blacklist" label="黑名单" placeholder="USDC,FDUSD 或 OPNUSDT" help="这些币种永不新开仓，优先级高于候选排序。" full />
            <SettingsField {...fieldProps} name="market_filter_assets" label="大盘过滤币种" help="用于判断大盘环境，默认 BTC 和 ETH。" />
            <SettingsField {...fieldProps} name="market_filter_min_change_pct" label="大盘最低涨幅 %" type="number" step="0.1" help="低于该 24h 涨幅时暂停追涨开仓。" />
            <SettingsField {...fieldProps} name="max_spread_bps" label="最大点差 bps" type="number" min="0" step="1" help="盘口点差超过该阈值时跳过入场；填 0 关闭点差过滤。" />
            <SettingsField {...fieldProps} name="min_orderbook_depth_usdt" label="最小盘口深度 USDT" type="number" min="0" step="100" help="买一侧深度不足时跳过入场；填 0 关闭深度过滤。" />
            <div className="toggle-grid">
              <SettingsToggle {...toggleProps} name="market_filter_enabled" label="启用 BTC/ETH 大盘过滤" />
              <SettingsToggle {...toggleProps} name="market_filter_require_all" label="要求全部大盘币满足" />
              <SettingsToggle {...toggleProps} name="account_sync_enabled" label="实盘成交后账户同步" />
            </div>
          </SettingsSection>
        )}
        {activeTab === "risk" && (
          <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="风控退出" description="控制初始止损、保本、移动止盈和开仓节流；止盈为 0 时让移动止损负责退出。">
            <SettingsField {...fieldProps} name="initial_stop_loss_pct" label="初始止损 %" type="number" min="0.1" step="0.1" />
            <SettingsField {...fieldProps} name="contract_max_margin_loss_pct" label="合约最大保证金亏损 %" type="number" min="0" step="0.1" help="用于合约实盘和 dry-run 合约模拟；按保证金亏损反推有效价格止损，例如 10x + 20% = 2% 价格止损。" />
            <SettingsField {...fieldProps} name="liquidation_stop_buffer_pct" label="强平止损缓冲 %" type="number" min="0" step="0.1" help="有效止损会保持在预估强平价上方，避免止损价低于或贴近强平价。" />
            <SettingsField {...fieldProps} name="take_profit_pct" label="止盈 %" type="number" min="0" step="0.1" />
            <SettingsField {...fieldProps} name="breakeven_trigger_pct" label="保本触发 %" type="number" min="0" step="0.1" help="最高价达到该涨幅后，把动态止损抬到成本附近；填 0 关闭。" />
            <SettingsField {...fieldProps} name="breakeven_offset_pct" label="保本偏移 %" type="number" step="0.1" help="保本止损相对开仓价的偏移，0 表示刚好成本价。" />
            <SettingsField {...fieldProps} name="trailing_start_pct" label="移动止盈启动 %" type="number" min="0" step="0.1" help="最高价达到该涨幅后启用移动止盈。" />
            <SettingsField {...fieldProps} name="trailing_stop_pct" label="移动止盈回撤 %" type="number" min="0" step="0.1" help="从最高价回撤该比例时卖出；填 0 关闭。" />
            <SettingsField {...fieldProps} name="fixed_stop_loss_usdt" label="固定止损 USDT" type="number" min="1" step="1" help="仅在固定止损模式启用后生效；建议为单笔金额的 10%-25%。" />
            <SettingsField {...fieldProps} name="fixed_stop_equity_usdt" label="权益触发 USDT" type="number" min="0" step="1" help="留空则不按账户权益切换固定止损。" />
            <SettingsField {...fieldProps} name="cooldown_minutes" label="冷却分钟" type="number" min="0" step="1" help="同一币种卖出后暂停重新开仓；填 0 关闭。" />
            <SettingsField {...fieldProps} name="max_daily_trades" label="每日最大开仓" type="number" min="0" step="1" help="按 UTC 日期统计买入次数；填 0 关闭。" />
            <SettingsField {...fieldProps} name="max_daily_loss_usdt" label="每日最大亏损 USDT" type="number" min="0" step="1" help="已实现亏损达到后停止新开仓；填 0 关闭。" full />
            <SettingsField {...fieldProps} name="max_total_exposure_pct" label="最大总敞口 %" type="number" min="0" step="1" help="所有持仓加本次拟开仓占权益估算的上限；填 0 关闭。" />
            <SettingsField {...fieldProps} name="max_symbol_exposure_pct" label="最大单币敞口 %" type="number" min="0" step="1" help="单一币种持仓加本次拟开仓占权益估算的上限；填 0 关闭。" />
            <SettingsField {...fieldProps} name="max_consecutive_losses" label="最大连亏次数" type="number" min="0" step="1" help="连续亏损达到该次数后暂停新开仓；填 0 关闭。" />
            <SettingsField {...fieldProps} name="max_intraday_drawdown_pct" label="日内回撤熔断 %" type="number" min="0" step="0.1" help="已实现亏损加浮亏达到该回撤比例后暂停新开仓；填 0 关闭。" />
            <SettingsField {...fieldProps} name="risk_per_trade_pct" label="风险定仓建议 %" type="number" min="0" step="0.1" help="仅计算建议仓位，不改变当前固定金额下单。" />
            <SettingsField {...fieldProps} name="oco_stop_limit_slippage_pct" label="OCO stop-limit slippage %" type="number" min="0" step="0.1" help="Live protection stop-limit price offset after stop trigger." />
            <div className="toggle-grid">
              <SettingsToggle {...toggleProps} name="fixed_stop_after_first_round_trip" label="首回合后固定止损" />
              <SettingsToggle {...toggleProps} name="exchange_protection_enabled" label="Exchange protection orders" />
            </div>
          </SettingsSection>
        )}
        {activeTab === "cost" && (
          <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="交易成本" description="用于 dry-run 估算真实成交偏差和手续费。">
            <SettingsField {...fieldProps} name="fee_rate_pct" label="手续费 %" type="number" min="0" step="0.01" help="dry-run 估算手续费，影响模拟成本和绩效统计。" />
            <SettingsField {...fieldProps} name="slippage_pct" label="滑点 %" type="number" min="0" step="0.01" help="dry-run 买入上浮、卖出下调，用于贴近真实成交。" />
          </SettingsSection>
        )}
        {activeTab === "runtime" && (
          <SettingsSection onSave={saveCurrentSettings} saveMessage={saveMessage} title="运行模式" description="控制循环频率、签名窗口、测试网、实盘和广场抓取方式。">
            <SettingsField {...fieldProps} name="poll_seconds" label="轮询秒数" type="number" min="5" step="1" />
            <SettingsField {...fieldProps} name="recv_window_ms" label="签名窗口 ms" type="number" min="1000" step="100" />
            <SettingsField {...fieldProps} name="signal_record_file" label="Signal record file" full />
            <div className="toggle-grid">
              <SettingsToggle {...toggleProps} name="testnet" label="Testnet" />
              <SettingsToggle {...toggleProps} name="live" label="Live 实盘" />
              <SettingsToggle {...toggleProps} name="signal_recording_enabled" label="Record signal JSONL" />
            </div>
          </SettingsSection>
        )}
      </div>
    </div>
  );
}

function SettingsField({
  name,
  label,
  settings,
  updateSetting,
  setFixedStopEdited,
  help,
  type = "text",
  min,
  max,
  step,
  placeholder,
  full = false,
}: {
  name: keyof SettingsState;
  label: string;
  settings: SettingsState;
  updateSetting: <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => void;
  setFixedStopEdited: (value: boolean) => void;
  help?: string;
  type?: string;
  min?: string;
  max?: string;
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
        max={max}
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

function SettingsToggle({
  name,
  label,
  settings,
  updateSetting,
}: {
  name: keyof SettingsState;
  label: string;
  settings: SettingsState;
  updateSetting: <K extends keyof SettingsState>(key: K, value: SettingsState[K]) => void;
}) {
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

function SettingsSection({
  title,
  description,
  children,
  onSave,
  saveMessage,
}: {
  title: string;
  description: string;
  children: ReactNode;
  onSave: () => void;
  saveMessage: string;
}) {
  return (
    <section className="settings-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Configuration</p>
          <h2>{title}</h2>
          <span>{description}</span>
        </div>
        <div className="settings-save-row">
          {saveMessage ? <small>{saveMessage}</small> : null}
          <button className="action-button" type="button" onClick={onSave}>
            保存设置
          </button>
        </div>
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
    trade_market_mode: textValue(config.trade_market_mode) || DEFAULT_SETTINGS.trade_market_mode,
    futures_margin_type: textValue(config.futures_margin_type) || DEFAULT_SETTINGS.futures_margin_type,
    order_quote_amount: textValue(config.order_quote_amount) || DEFAULT_SETTINGS.order_quote_amount,
    dry_run_initial_equity_usdt: textValue(config.dry_run_initial_equity_usdt) || DEFAULT_SETTINGS.dry_run_initial_equity_usdt,
    max_open_positions: textValue(config.max_open_positions) || DEFAULT_SETTINGS.max_open_positions,
    leverage_multiplier: textValue(config.leverage_multiplier) || DEFAULT_SETTINGS.leverage_multiplier,
    contract_max_margin_loss_pct: textValue(config.contract_max_margin_loss_pct) || DEFAULT_SETTINGS.contract_max_margin_loss_pct,
    liquidation_stop_buffer_pct: textValue(config.liquidation_stop_buffer_pct) || DEFAULT_SETTINGS.liquidation_stop_buffer_pct,
    contract_simulation_enabled: config.contract_simulation_enabled !== false,
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
    max_total_exposure_pct: textValue(config.max_total_exposure_pct) || DEFAULT_SETTINGS.max_total_exposure_pct,
    max_symbol_exposure_pct: textValue(config.max_symbol_exposure_pct) || DEFAULT_SETTINGS.max_symbol_exposure_pct,
    max_consecutive_losses: textValue(config.max_consecutive_losses) || DEFAULT_SETTINGS.max_consecutive_losses,
    max_intraday_drawdown_pct: textValue(config.max_intraday_drawdown_pct) || DEFAULT_SETTINGS.max_intraday_drawdown_pct,
    risk_per_trade_pct: textValue(config.risk_per_trade_pct) || DEFAULT_SETTINGS.risk_per_trade_pct,
    fee_rate_pct: textValue(config.fee_rate_pct) || DEFAULT_SETTINGS.fee_rate_pct,
    slippage_pct: textValue(config.slippage_pct) || DEFAULT_SETTINGS.slippage_pct,
    poll_seconds: textValue(config.poll_seconds) || DEFAULT_SETTINGS.poll_seconds,
    recv_window_ms: textValue(config.recv_window_ms) || DEFAULT_SETTINGS.recv_window_ms,
    testnet: textValue(config.base_url).includes("testnet"),
    live: config.dry_run === false,
    square_browser_mode: config.square_browser_mode !== false,
    square_diagnostic_limit: textValue(config.square_diagnostic_limit) || DEFAULT_SETTINGS.square_diagnostic_limit,
    telegram_bot_token: "",
    telegram_chat_id: textValue(config.telegram_chat_id),
    dashboard_auth_token: "",
    signal_recording_enabled: config.signal_recording_enabled !== false,
    signal_record_file: textValue(config.signal_record_file) || DEFAULT_SETTINGS.signal_record_file,
    telegram_enabled: Boolean(config.telegram_enabled),
    fixed_stop_after_first_round_trip: Boolean(config.fixed_stop_after_first_round_trip),
    market_filter_enabled: Boolean(config.market_filter_enabled),
    market_filter_require_all: Boolean(config.market_filter_require_all),
    account_sync_enabled: config.account_sync_enabled !== false,
    kline_confirmation_enabled: config.kline_confirmation_enabled !== false,
    min_square_confidence_score: textValue(config.min_square_confidence_score) || DEFAULT_SETTINGS.min_square_confidence_score,
    max_spread_bps: textValue(config.max_spread_bps) || DEFAULT_SETTINGS.max_spread_bps,
    min_orderbook_depth_usdt: textValue(config.min_orderbook_depth_usdt) || DEFAULT_SETTINGS.min_orderbook_depth_usdt,
    exchange_protection_enabled: config.exchange_protection_enabled !== false,
    oco_stop_limit_slippage_pct: textValue(config.oco_stop_limit_slippage_pct) || DEFAULT_SETTINGS.oco_stop_limit_slippage_pct,
  };
}

function loadSavedSettings(): Partial<SettingsState> {
  try {
    const raw = localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Partial<SettingsState>;
    if (!localStorage.getItem(SETTINGS_BROWSER_DEFAULT_MIGRATION_KEY)) {
      parsed.square_browser_mode = true;
      localStorage.setItem(SETTINGS_BROWSER_DEFAULT_MIGRATION_KEY, "1");
    }
    if (!localStorage.getItem(SETTINGS_CONTRACT_DEFAULT_MIGRATION_KEY)) {
      if (parsed.initial_stop_loss_pct === "20") parsed.initial_stop_loss_pct = DEFAULT_SETTINGS.initial_stop_loss_pct;
      if (parsed.take_profit_pct === "12") parsed.take_profit_pct = DEFAULT_SETTINGS.take_profit_pct;
      if (parsed.breakeven_trigger_pct === "6") parsed.breakeven_trigger_pct = DEFAULT_SETTINGS.breakeven_trigger_pct;
      if (parsed.breakeven_offset_pct === "0") parsed.breakeven_offset_pct = DEFAULT_SETTINGS.breakeven_offset_pct;
      if (parsed.trailing_start_pct === "8") parsed.trailing_start_pct = DEFAULT_SETTINGS.trailing_start_pct;
      if (parsed.trailing_stop_pct === "5") parsed.trailing_stop_pct = DEFAULT_SETTINGS.trailing_stop_pct;
      parsed.contract_simulation_enabled = parsed.contract_simulation_enabled !== false;
      localStorage.setItem(SETTINGS_CONTRACT_DEFAULT_MIGRATION_KEY, "1");
    }
    if (!localStorage.getItem(SETTINGS_PRESET_DEFAULT_MIGRATION_KEY)) {
      if (parsed.leverage_multiplier === "10") parsed.leverage_multiplier = DEFAULT_SETTINGS.leverage_multiplier;
      if (parsed.max_open_positions === "10" || parsed.max_open_positions === "5" || parsed.max_open_positions === "1") {
        parsed.max_open_positions = DEFAULT_SETTINGS.max_open_positions;
      }
      localStorage.setItem(SETTINGS_PRESET_DEFAULT_MIGRATION_KEY, "1");
    }
    const allowedKeys = new Set(Object.keys(DEFAULT_SETTINGS));
    return Object.fromEntries(Object.entries(parsed).filter(([key]) => allowedKeys.has(key))) as Partial<SettingsState>;
  } catch {
    return {};
  }
}

function loadFavoriteSymbols(): string[] {
  try {
    const raw = localStorage.getItem(FAVORITES_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return Array.from(new Set(parsed.map((item) => normalizeSymbol(item)).filter(Boolean))).sort();
  } catch {
    return [];
  }
}

function saveSettings(settings: SettingsState) {
  localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  localStorage.setItem(SETTINGS_BROWSER_DEFAULT_MIGRATION_KEY, "1");
  localStorage.setItem(SETTINGS_CONTRACT_DEFAULT_MIGRATION_KEY, "1");
  localStorage.setItem(SETTINGS_PRESET_DEFAULT_MIGRATION_KEY, "1");
}

function formatDefaultFixedStop(value: number): string {
  const rounded = Math.max(1, value * 0.2);
  return Number.isInteger(rounded) ? String(rounded) : rounded.toFixed(2).replace(/\.?0+$/, "");
}

function buildPositionViews(positions: Position[], snapshots: PositionSnapshot[]): PositionView[] {
  const snapshotBySymbol = new Map(snapshots.filter((item) => item?.symbol).map((item) => [String(item.symbol), item]));
  const seen = new Set<string>();
  const result: PositionView[] = [];
  for (const position of positions) {
    const symbol = String(position.symbol || "");
    if (!symbol) {
      continue;
    }
    const snapshot = snapshotBySymbol.get(symbol);
    seen.add(symbol);
    result.push({
      symbol,
      baseAsset: String(position.base_asset || snapshot?.base_asset || symbol.replace(/USDT$/, "")),
      quantity: snapshot?.quantity ?? position.quantity,
      entryPrice: snapshot?.entry_price ?? position.entry_price,
      highestPrice: snapshot?.highest_price ?? position.highest_price,
      quoteSpent: snapshot?.quote_spent ?? position.quote_spent,
      openedAt: position.opened_at,
      snapshot,
      modeLabel: snapshot?.mode_label,
    });
  }
  for (const snapshot of snapshots) {
    const symbol = String(snapshot.symbol || "");
    if (!symbol || seen.has(symbol)) {
      continue;
    }
    result.push({
      symbol,
      baseAsset: String(snapshot.base_asset || symbol.replace(/USDT$/, "")),
      quantity: snapshot.quantity,
      entryPrice: snapshot.entry_price,
      highestPrice: snapshot.highest_price,
      quoteSpent: snapshot.quote_spent,
      openedAt: snapshot.opened_at,
      snapshot,
      modeLabel: snapshot.mode_label,
    });
  }
  return result;
}

function positionTotals(positions: PositionView[]) {
  let marketValue = 0;
  let unrealizedPnl = 0;
  let quoteSpent = 0;
  let hasMarketValue = false;
  let hasPnl = false;
  for (const item of positions) {
    const itemMarketValue = asNumber(item.snapshot?.market_value);
    if (itemMarketValue !== null) {
      marketValue += itemMarketValue;
      hasMarketValue = true;
    }
    const itemPnl = asNumber(item.snapshot?.unrealized_pnl);
    if (itemPnl !== null) {
      unrealizedPnl += itemPnl;
      hasPnl = true;
    }
    const itemQuoteSpent = asNumber(item.quoteSpent);
    if (itemQuoteSpent !== null) {
      quoteSpent += itemQuoteSpent;
    }
  }
  return {
    marketValue: hasMarketValue ? marketValue : null,
    unrealizedPnl: hasPnl ? unrealizedPnl : null,
    unrealizedPnlPct: quoteSpent > 0 && hasPnl ? (unrealizedPnl / quoteSpent) * 100 : null,
  };
}

function accountRiskCapacitySummary(accountRisk: AccountRiskSnapshot | null): string {
  const quoteAsset = textValue(accountRisk?.quote_asset) || "USDT";
  if (accountRisk?.dry_run_max_notional_quote) {
    return [
      `最大名义 ${formatMoney(accountRisk.dry_run_max_notional_quote, quoteAsset)}`,
      `可用保证金 ${formatMoney(accountRisk.available_margin_quote ?? 0, quoteAsset)}`,
      `可用名义 ${formatMoney(accountRisk.available_notional_quote ?? 0, quoteAsset)}`,
    ].join(" · ");
  }
  return `敞口 ${formatPercent(accountRisk?.total_exposure_pct ?? 0)} · 建议 ${formatMoney(accountRisk?.risk_based_quote_suggestion ?? 0, quoteAsset)}`;
}

function riskSummary(snapshot: PositionSnapshot | null, guard: EntryGuardSnapshot | null, roundTrips: unknown): string {
  const parts = [];
  if (snapshot?.liquidation_triggered) {
    parts.push("已触发强平");
  } else if (snapshot?.stop_triggered) {
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
  if (snapshot?.liquidation_distance_pct) {
    parts.push(`距强平 ${formatPercent(snapshot.liquidation_distance_pct)}`);
  }
  if (snapshot?.take_profit_price) {
    parts.push(`止盈 ${formatPrice(snapshot.take_profit_price)}`);
  }
  if (snapshot?.take_profit_distance_pct) {
    parts.push(`距止盈 ${formatPercent(snapshot.take_profit_distance_pct)}`);
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
