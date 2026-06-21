#!/usr/bin/env python3
"""Export the local SQLite trade journal to CSV."""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import binance_square_momentum_bot as bot


ROUND_COLUMNS = [
    "trade_id",
    "strategy_version",
    "entry_event_id",
    "exit_event_id",
    "symbol",
    "market_type",
    "position_mode",
    "dry_run",
    "entry_time",
    "exit_time",
    "quantity",
    "entry_price",
    "exit_price",
    "entry_amount",
    "exit_amount",
    "fee_amount",
    "pnl",
    "return_pct",
    "exit_reason",
    "partial_exit_count",
    "duration_seconds",
]

LEGACY_ROUND_COLUMNS = [
    "id",
    "symbol",
    "market_type",
    "position_mode",
    "dry_run",
    "entry_time",
    "exit_time",
    "quantity",
    "entry_price",
    "exit_price",
    "entry_amount",
    "exit_amount",
    "fee_amount",
    "pnl",
    "return_pct",
    "exit_reason",
    "duration_seconds",
]

EVENT_COLUMNS = [
    "id",
    "event_uid",
    "ts",
    "action",
    "symbol",
    "market_type",
    "position_mode",
    "dry_run",
    "quantity",
    "price",
    "fee_amount",
    "fee_asset",
    "quote_amount",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Export trade_journal.sqlite3 to CSV")
    parser.add_argument("path", nargs="?", default=bot.DEFAULT_TRADE_JOURNAL_FILE)
    parser.add_argument(
        "--view",
        choices=("complete_trades", "round_trips", "events"),
        default="complete_trades",
    )
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    items = []
    offset = 0
    while True:
        page = bot.query_trade_journal(args.path, args.view, 500, offset)
        batch = page.get("items", [])
        items.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(page.get("total") or 0):
            break
    columns = (
        EVENT_COLUMNS
        if args.view == "events"
        else LEGACY_ROUND_COLUMNS
        if args.view == "round_trips"
        else ROUND_COLUMNS
    )
    output_path = Path(args.output or f"trade_journal_{args.view}.csv")
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(items)
    print(f"exported {len(items)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
