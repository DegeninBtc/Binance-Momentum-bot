export type Primitive = string | number | boolean | null | undefined;

export type ConfigPayload = Record<string, Primitive | string[]>;

export type SettingsState = {
  quote_asset: string;
  order_quote_amount: string;
  max_open_positions: string;
  leverage_multiplier: string;
  state_file: string;
  min_price_change_percent: string;
  min_volatility_percent: string;
  min_quote_volume: string;
  top_post_limit: string;
  top_coin_limit: string;
  asset_whitelist: string;
  asset_blacklist: string;
  market_filter_assets: string;
  market_filter_min_change_pct: string;
  initial_stop_loss_pct: string;
  take_profit_pct: string;
  breakeven_trigger_pct: string;
  breakeven_offset_pct: string;
  trailing_start_pct: string;
  trailing_stop_pct: string;
  fixed_stop_loss_usdt: string;
  fixed_stop_equity_usdt: string;
  cooldown_minutes: string;
  max_daily_trades: string;
  max_daily_loss_usdt: string;
  fee_rate_pct: string;
  slippage_pct: string;
  poll_seconds: string;
  recv_window_ms: string;
  testnet: boolean;
  live: boolean;
  square_browser_mode: boolean;
  telegram_bot_token: string;
  telegram_chat_id: string;
  telegram_enabled: boolean;
  fixed_stop_after_first_round_trip: boolean;
  market_filter_enabled: boolean;
  market_filter_require_all: boolean;
  account_sync_enabled: boolean;
};

export type Candidate = {
  symbol?: string;
  asset?: string;
  price_change_percent?: Primitive;
  combined_score?: Primitive;
  volatility_percent?: Primitive;
};

export type HotAsset = {
  symbol?: string;
  asset?: string;
  score?: Primitive;
  market_score?: Primitive;
  square_score?: Primitive;
  mentions?: Primitive;
  price_change_percent?: Primitive;
  volatility_percent?: Primitive;
};

export type LastSignal = {
  candidate?: Candidate | null;
  hot_assets?: HotAsset[];
  source?: string;
  note?: string;
  checked_at?: string;
};

export type Position = {
  symbol?: string;
  base_asset?: string;
  quantity?: Primitive;
  entry_price?: Primitive;
  quote_spent?: Primitive;
  highest_price?: Primitive;
  opened_at?: string;
};

export type PositionSnapshot = {
  symbol?: string;
  base_asset?: string;
  quote_asset?: string;
  dry_run?: boolean;
  mode_label?: string;
  quantity?: Primitive;
  entry_price?: Primitive;
  highest_price?: Primitive;
  quote_spent?: Primitive;
  opened_at?: string;
  current_price?: Primitive;
  price_error?: string;
  market_value?: Primitive;
  unrealized_pnl?: Primitive;
  unrealized_pnl_pct?: Primitive;
  leveraged_unrealized_pnl_pct?: Primitive;
  leverage_multiplier?: Primitive;
  active_stop_mode?: string;
  dynamic_stop_price?: Primitive;
  take_profit_price?: Primitive;
  stop_distance_pct?: Primitive;
  stop_triggered?: boolean;
  take_profit_distance_pct?: Primitive;
  take_profit_triggered?: boolean;
};

export type EntryGuardSnapshot = {
  buy_count?: Primitive;
  realized_pnl?: Primitive;
  max_daily_trades?: Primitive;
  max_daily_loss_usdt?: Primitive;
  cooldown_minutes?: Primitive;
  trade_limit_hit?: boolean;
  loss_limit_hit?: boolean;
  entry_blocked?: boolean;
};

export type PerformanceStats = {
  quote_asset?: string;
  completed_trades?: Primitive;
  wins?: Primitive;
  losses?: Primitive;
  win_rate?: Primitive;
  total_pnl?: Primitive;
  gross_profit?: Primitive;
  gross_loss?: Primitive;
  avg_pnl?: Primitive;
  avg_return_pct?: Primitive;
  profit_factor?: Primitive;
  best_trade?: Primitive;
  worst_trade?: Primitive;
  max_drawdown?: Primitive;
  current_streak?: Primitive;
  current_streak_type?: string;
};

export type TradeItem = {
  ts?: string;
  dry_run?: boolean;
  action?: string;
  symbol?: string;
  quantity?: Primitive;
  price?: Primitive;
  fee_amount?: Primitive;
  fee_asset?: string;
  quote_amount?: Primitive;
};

export type DiagnosticsUrl = {
  url?: string;
  status_code?: Primitive;
  content_length?: Primitive;
  json_posts?: Primitive;
  html_posts?: Primitive;
  error?: string;
};

export type DiagnosticsSample = {
  title?: string;
  text?: string;
};

export type Diagnostics = {
  mode?: string;
  total_posts?: Primitive;
  raw_posts?: Primitive;
  filtered_out_posts?: Primitive;
  browser_posts_raw?: Primitive;
  browser_error?: string;
  hint?: string;
  urls?: DiagnosticsUrl[];
  samples?: DiagnosticsSample[];
};

export type BotState = {
  position?: Position | null;
  positions?: Position[];
  position_snapshot?: PositionSnapshot | null;
  position_snapshots?: PositionSnapshot[];
  entry_guard_snapshot?: EntryGuardSnapshot | null;
  performance_stats?: PerformanceStats | null;
  trade_log?: TradeItem[];
  completed_round_trips?: Primitive;
};

export type DashboardStatus = {
  running?: boolean;
  mode?: string;
  last_error?: string;
  last_started_at?: string;
  last_finished_at?: string;
  last_signal?: LastSignal | null;
  last_diagnostics?: Diagnostics | null;
  logs?: string[];
  config?: ConfigPayload;
  state?: BotState;
  error?: string;
};

export type TabKey = "hot" | "trades" | "diag" | "logs" | "settings";

export type SettingsTabKey = "basic" | "signal" | "scope" | "risk" | "cost" | "runtime" | "notify";
