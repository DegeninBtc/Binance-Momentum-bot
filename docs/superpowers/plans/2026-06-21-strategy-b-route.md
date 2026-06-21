# Strategy B Route Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build trustworthy lifecycle statistics, hard account-risk limits, volatility-normalized entries, and a usable out-of-sample validation loop.

**Architecture:** Extend the existing event journal without deleting raw events, derive complete trades by `trade_id`, and keep execution logic in the existing bot while exposing pure calculation helpers for tests. Entry and position sizing decisions remain deterministic and configuration-driven; adaptive changes apply to dry-run first.

**Tech Stack:** Python 3, SQLite, `Decimal`, Binance REST market data, React/TypeScript, Vite.

## Global Constraints

- Preserve the current adaptive `+2R` partial take profit and `3 × ATR` trailing exit.
- Do not delete historical audit events.
- Do not enable the new entry strategy for live trading.
- Use only completed candles for entry indicators.
- Every behavior change starts with a failing regression test.

---

### Task 1: Trustworthy trade lifecycle

**Files:**
- Modify: `binance_square_momentum_bot.py`
- Modify: `tools/analyze_trade_journal.py`
- Test: `tests/test_safety_and_risk.py`

**Interfaces:**
- Produces `trade_id`, `strategy_version`, `is_synthetic` event fields.
- Produces `build_complete_trades_from_events(events)` for analytics and Dashboard stats.

- [ ] Add failing tests for partial exits, full-close residual quantity, and synthetic test exclusion.
- [ ] Migrate SQLite columns without deleting old events.
- [ ] Propagate `trade_id` and strategy version to every event.
- [ ] Close the full dry-run quantity on full exits.
- [ ] Build one completed trade per entry lifecycle and update journal analytics.
- [ ] Run the safety/risk tests.

### Task 2: Account risk and risk-based sizing

**Files:**
- Modify: `binance_square_momentum_bot.py`
- Modify: `web_dashboard.py`
- Modify: `web/src/App.tsx`
- Modify: `web/src/types.ts`
- Test: `tests/test_safety_and_risk.py`

**Interfaces:**
- Produces `risk_based_order_size(config, equity, entry_price, stop_price, leverage)`.
- Adds daily loss percentage and consecutive-loss pause settings.

- [ ] Add failing tests for 0.75% risk sizing and each account guard.
- [ ] Implement risk-based sizing capped by the fixed order amount.
- [ ] Set defaults: 5× leverage, 4 positions, 100% total exposure, 25% symbol exposure, 12 daily entries, 2% daily loss, 3-loss/4-hour pause, 60-minute loss cooldown.
- [ ] Surface guard reasons and settings in the Dashboard.
- [ ] Run Python and TypeScript tests.

### Task 3: Volatility-normalized entry

**Files:**
- Modify: `binance_square_momentum_bot.py`
- Modify: `web_dashboard.py`
- Test: `tests/test_safety_and_risk.py`

**Interfaces:**
- Extends kline snapshots with ATR, EMA distance in ATR, and candle range in ATR.
- Adds overextension thresholds and an early-failure decision helper.

- [ ] Add failing tests proving the current open candle is ignored.
- [ ] Add failing tests for ROC, EMA-distance, and candle-range rejection.
- [ ] Compute indicators from completed candles only.
- [ ] Apply overextension checks only to dry-run.
- [ ] Add the 15-minute `+0.5R` early-failure exit.
- [ ] Run the complete Python regression suite.

### Task 4: Futures-aware validation

**Files:**
- Modify: `binance_square_momentum_bot.py`
- Modify: `tools/analyze_signal_records.py`
- Modify: `tools/replay_signal_records.py`
- Modify: `tools/walk_forward_signal_records.py`
- Test: `tests/test_safety_and_risk.py`

**Interfaces:**
- Future-return updater selects market client from `candidate.market_type`.
- Reports complete-trade and signal feature cohorts.

- [ ] Add a failing Futures-client routing test.
- [ ] Route Spot and Futures records to their corresponding K-line endpoint.
- [ ] Add strategy version and entry-feature fields to analysis output.
- [ ] Ensure walk-forward reports empty labels explicitly rather than presenting unusable results as validation.
- [ ] Run analysis-tool tests and syntax checks.

### Task 5: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: Dashboard UI files as needed.

- [ ] Document new defaults, formulas, and dry-run rollout boundary.
- [ ] Run `python3 tests/test_safety_and_risk.py`.
- [ ] Run Python compilation checks.
- [ ] Run TypeScript checking and Vite production build.
- [ ] Run `git diff --check` and audit SQLite compatibility.

