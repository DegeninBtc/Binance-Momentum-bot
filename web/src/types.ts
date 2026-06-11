export type Primitive = string | number | boolean | null | undefined;

export type ConfigPayload = Record<string, Primitive | string[]>;

export type SettingsState = {
  quote_asset: string;
  trade_market_mode: string;
  futures_margin_type: string;
  order_quote_amount: string;
  max_open_positions: string;
  leverage_multiplier: string;
  contract_max_margin_loss_pct: string;
  liquidation_stop_buffer_pct: string;
  contract_simulation_enabled: boolean;
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
  max_total_exposure_pct: string;
  max_symbol_exposure_pct: string;
  max_consecutive_losses: string;
  max_intraday_drawdown_pct: string;
  risk_per_trade_pct: string;
  fee_rate_pct: string;
  slippage_pct: string;
  poll_seconds: string;
  recv_window_ms: string;
  testnet: boolean;
  live: boolean;
  square_browser_mode: boolean;
  square_diagnostic_limit: string;
  telegram_bot_token: string;
  telegram_chat_id: string;
  dashboard_auth_token: string;
  signal_recording_enabled: boolean;
  signal_record_file: string;
  telegram_enabled: boolean;
  fixed_stop_after_first_round_trip: boolean;
  market_filter_enabled: boolean;
  market_filter_require_all: boolean;
  account_sync_enabled: boolean;
  kline_confirmation_enabled: boolean;
  min_square_confidence_score: string;
  max_spread_bps: string;
  min_orderbook_depth_usdt: string;
  exchange_protection_enabled: boolean;
  oco_stop_limit_slippage_pct: string;
};

export type Candidate = {
  symbol?: string;
  asset?: string;
  price_change_percent?: Primitive;
  combined_score?: Primitive;
  volatility_percent?: Primitive;
  market_type?: string;
  last_price?: Primitive;
};

export type HotAsset = {
  symbol?: string;
  asset?: string;
  score?: Primitive;
  market_score?: Primitive;
  square_score?: Primitive;
  mentions?: Primitive;
  last_price?: Primitive;
  price_change_percent?: Primitive;
  volatility_percent?: Primitive;
  market_type?: string;
};

export type ChartRangeKey = "1H" | "6H" | "24H" | "7D" | "30D";

export type MarketChartPoint = {
  time?: Primitive;
  open?: Primitive;
  high?: Primitive;
  low?: Primitive;
  close?: Primitive;
};

export type MarketChart = {
  symbol?: string;
  range?: ChartRangeKey;
  interval?: string;
  points?: MarketChartPoint[];
  first_close?: Primitive;
  last_close?: Primitive;
  high?: Primitive;
  low?: Primitive;
  change_percent?: Primitive;
  error?: string;
};

export type LastSignal = {
  candidate?: Candidate | null;
  hot_assets?: HotAsset[];
  entry_confirmation?: EntryConfirmation | null;
  square_confidence?: SquareConfidence | null;
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
  position_mode?: string;
  market_type?: string;
  margin_type?: string;
  contract_simulation?: boolean;
  quantity?: Primitive;
  entry_price?: Primitive;
  highest_price?: Primitive;
  quote_spent?: Primitive;
  margin_quote?: Primitive;
  notional_quote?: Primitive;
  opened_at?: string;
  current_price?: Primitive;
  price_error?: string;
  market_value?: Primitive;
  unrealized_pnl?: Primitive;
  unrealized_pnl_pct?: Primitive;
  price_change_pct?: Primitive;
  leveraged_unrealized_pnl_pct?: Primitive;
  leverage_multiplier?: Primitive;
  liquidation_price?: Primitive;
  active_stop_mode?: string;
  configured_stop_price?: Primitive;
  initial_stop_loss_pct?: Primitive;
  configured_stop_loss_pct?: Primitive;
  effective_stop_loss_pct?: Primitive;
  margin_loss_stop_pct?: Primitive;
  liquidation_distance_pct?: Primitive;
  max_safe_stop_loss_pct?: Primitive;
  contract_max_margin_loss_pct?: Primitive;
  liquidation_stop_buffer_pct?: Primitive;
  stop_guard_tightened?: boolean;
  stop_guard_warning?: string;
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
  trade_count?: Primitive;
  event_count?: Primitive;
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
  journal_enabled?: boolean;
  journal_file?: string;
};

export type TradeItem = {
  id?: Primitive;
  event_uid?: string;
  ts?: string;
  dry_run?: boolean;
  action?: string;
  symbol?: string;
  market_type?: string;
  position_mode?: string;
  quantity?: Primitive;
  price?: Primitive;
  fee_amount?: Primitive;
  fee_asset?: string;
  quote_amount?: Primitive;
};

export type TradeRoundTrip = {
  id?: Primitive;
  entry_event_id?: Primitive;
  exit_event_id?: Primitive;
  symbol?: string;
  market_type?: string;
  position_mode?: string;
  dry_run?: boolean | Primitive;
  entry_time?: string;
  exit_time?: string;
  quantity?: Primitive;
  entry_price?: Primitive;
  exit_price?: Primitive;
  entry_amount?: Primitive;
  exit_amount?: Primitive;
  fee_amount?: Primitive;
  pnl?: Primitive;
  return_pct?: Primitive;
  exit_reason?: string;
  duration_seconds?: Primitive;
};

export type TradeJournalSummary = {
  enabled?: boolean;
  file?: string;
  event_count?: Primitive;
  round_trip_count?: Primitive;
  error?: string;
};

export type TradeJournalPage = {
  view?: "round_trips" | "events";
  items?: Array<TradeItem | TradeRoundTrip>;
  total?: Primitive;
  limit?: Primitive;
  offset?: Primitive;
  db_path?: string;
  stats?: PerformanceStats;
  error?: string;
};

export type PendingOrder = {
  symbol?: string;
  side?: string;
  client_order_id?: string;
  quote_amount?: Primitive;
  quantity?: Primitive;
  created_at?: string;
  action?: string;
  status?: string;
  error?: string;
};

export type ProtectionOrder = {
  symbol?: string;
  client_order_id?: string;
  quantity?: Primitive;
  take_profit_price?: Primitive;
  stop_price?: Primitive;
  stop_limit_price?: Primitive;
  status?: string;
  kind?: string;
  dry_run?: boolean;
  created_at?: string;
  error?: string;
};

export type ApiKeyCheck = {
  api_key_loaded?: boolean;
  api_secret_loaded?: boolean;
  api_key_suffix?: string;
  can_trade?: boolean | null;
  can_withdraw?: boolean | null;
  spot_trading_allowed?: boolean | null;
  futures_account_accessible?: boolean | null;
  futures_error?: string;
  spot_error?: string;
  error?: string;
  warning?: string;
};

export type SquareConfidence = {
  score?: Primitive;
  post_count?: Primitive;
  structured_count?: Primitive;
  fresh_count?: Primitive;
  extractor_mode?: string;
  consecutive_failures?: Primitive;
  reasons?: string[];
  checked_at?: string;
};

export type EntryConfirmation = {
  passed?: boolean;
  symbol?: string;
  base_asset?: string;
  reason?: string;
  checks?: Record<string, unknown>;
  square_confidence?: SquareConfidence;
  checked_at?: string;
};

export type AccountRiskSnapshot = {
  entry_blocked?: boolean;
  reason?: string;
  quote_asset?: string;
  equity_estimate?: Primitive;
  total_exposure?: Primitive;
  total_exposure_pct?: Primitive;
  symbol_exposure_pct?: Primitive;
  realized_pnl_today?: Primitive;
  unrealized_pnl?: Primitive;
  intraday_drawdown?: Primitive;
  intraday_drawdown_pct?: Primitive;
  consecutive_losses?: Primitive;
  fixed_order_quote?: Primitive;
  risk_based_quote_suggestion?: Primitive;
  limits?: Record<string, Primitive>;
  checked_at?: string;
};

export type SafetySnapshot = {
  pending_order?: PendingOrder | null;
  pending_order_open?: boolean;
  protection_enabled?: boolean;
  protection_orders?: ProtectionOrder[];
  protected_symbols?: string[];
  missing_protection_symbols?: string[];
  failed_protection_orders?: ProtectionOrder[];
  protection_ok?: boolean;
  live_confirm_required?: boolean;
  live_confirmed?: boolean;
  api_key_check?: ApiKeyCheck;
  manual_checks?: string[];
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

export type DiagnosticsPostScoreBasis = {
  symbol_score?: Primitive;
  context_score?: Primitive;
  long_context_score?: Primitive;
  traffic_score?: Primitive;
  length_score?: Primitive;
  time_decay_score?: Primitive;
  time_weight?: Primitive;
  symbol_mentions?: Primitive;
  has_trading_context?: boolean;
  long_only_context?: boolean;
  text_length?: Primitive;
};

export type DiagnosticsPost = DiagnosticsSample & {
  score?: Primitive;
  traffic_score?: Primitive;
  url?: string;
  created_at?: string;
  post_id?: string;
  author?: string;
  source?: string;
  extractor_mode?: string;
  valid_trading_post?: boolean;
  filter_reasons?: string[];
  symbols?: Array<{ asset?: string; mentions?: Primitive }>;
  score_basis?: DiagnosticsPostScoreBasis;
};

export type Diagnostics = {
  mode?: string;
  checked_at?: string;
  display_limit?: Primitive;
  displayed_posts?: Primitive;
  total_posts?: Primitive;
  raw_posts?: Primitive;
  filtered_out_posts?: Primitive;
  browser_posts_raw?: Primitive;
  extractor_mode?: string;
  square_fetch_latency_ms?: Primitive;
  api_response_count?: Primitive;
  api_post_count?: Primitive;
  json_post_count?: Primitive;
  html_post_count?: Primitive;
  rendered_text_post_count?: Primitive;
  new_post_count?: Primitive;
  duplicate_post_count?: Primitive;
  latest_post_time?: string;
  consecutive_failures?: Primitive;
  browser_error?: string;
  hint?: string;
  signal_record_stats?: SignalRecordStats;
  urls?: DiagnosticsUrl[];
  samples?: DiagnosticsSample[];
  display_posts?: DiagnosticsPost[];
};

export type SignalRecordStats = {
  record_file?: string;
  record_count?: Primitive;
  entered_count?: Primitive;
  skipped_count?: Primitive;
  last_record_at?: string;
  updated_count?: Primitive;
  future_returns_count?: Primitive;
  decision_groups?: Record<string, Primitive>;
};

export type BotState = {
  position?: Position | null;
  positions?: Position[];
  position_snapshot?: PositionSnapshot | null;
  position_snapshots?: PositionSnapshot[];
  entry_guard_snapshot?: EntryGuardSnapshot | null;
  performance_stats?: PerformanceStats | null;
  safety_snapshot?: SafetySnapshot | null;
  entry_confirmation?: EntryConfirmation | null;
  square_confidence?: SquareConfidence | null;
  account_risk_snapshot?: AccountRiskSnapshot | null;
  trade_journal?: TradeJournalSummary | null;
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
  loop_snapshot?: LoopSnapshot | null;
  logs?: string[];
  config?: ConfigPayload;
  state?: BotState;
  dashboard_security?: DashboardSecurity | null;
  error?: string;
};

export type LoopSnapshot = {
  cycle_count?: Primitive;
  last_cycle_started_at?: string;
  last_cycle_finished_at?: string;
  next_cycle_eta?: string;
  last_cycle_action?: string;
  last_cycle_note?: string;
};

export type DashboardSecurity = {
  read_only?: boolean;
  token_enabled?: boolean;
  host_origin_check_enabled?: boolean;
  bound_host?: string;
  local_only_host?: boolean;
  allowed_hosts?: string[];
  warning?: string;
};

export type TabKey = "positions" | "hot" | "favorites" | "strategy" | "trades" | "diag" | "security" | "logs" | "notify" | "settings";

export type SettingsTabKey = "basic" | "signal" | "scope" | "risk" | "cost" | "runtime";
