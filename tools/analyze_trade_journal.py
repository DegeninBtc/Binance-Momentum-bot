#!/usr/bin/env python3
"""Print lightweight statistics for the local SQLite trade journal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import binance_square_momentum_bot as bot


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze trade_journal.sqlite3")
    parser.add_argument("path", nargs="?", default=bot.DEFAULT_TRADE_JOURNAL_FILE)
    parser.add_argument("--quote", default="USDT")
    args = parser.parse_args()

    stats = bot.trade_journal_stats(args.path, args.quote) or {
        "quote_asset": args.quote,
        "trade_count": 0,
        "event_count": 0,
        "total_pnl": "0",
    }
    rounds = bot.query_trade_journal(args.path, "round_trips", 500, 0).get("items", [])
    by_reason: dict[str, int] = {}
    by_symbol: dict[str, int] = {}
    for item in rounds:
        reason = str(item.get("exit_reason") or "unknown")
        symbol = str(item.get("symbol") or "unknown")
        by_reason[reason] = by_reason.get(reason, 0) + 1
        by_symbol[symbol] = by_symbol.get(symbol, 0) + 1

    payload = {
        "stats": stats,
        "exit_reasons": by_reason,
        "symbols": by_symbol,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
