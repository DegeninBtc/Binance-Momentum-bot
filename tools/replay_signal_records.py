#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import analyze_signal_records as analysis


def max_consecutive_losses(records: list[dict[str, Any]], horizon: str) -> int:
    longest = 0
    current = 0
    for record in records:
        if not analysis.entered(record):
            continue
        value = analysis.future_return(record, horizon)
        if value is None:
            continue
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def group_opportunity(records: list[dict[str, Any]], horizon: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[float]] = {}
    for record in records:
        value = analysis.future_return(record, horizon)
        if value is None:
            continue
        group = analysis.decision_group(record)
        groups.setdefault(group, []).append(value)
    result: dict[str, dict[str, Any]] = {}
    for group, values in groups.items():
        positive = [value for value in values if value > 0]
        negative = [value for value in values if value < 0]
        result[group] = {
            "count": len(values),
            "positive_count": len(positive),
            "negative_count": len(negative),
            "mean_return_pct": sum(values) / len(values) if values else None,
            "missed_upside_count": len(positive) if group != "entered" else 0,
            "avoided_downside_count": len(negative) if group != "entered" else 0,
        }
    return result


def replay(records: list[dict[str, Any]], horizon: str = "1h") -> dict[str, Any]:
    entered_records = [record for record in records if analysis.entered(record)]
    returns = [analysis.future_return(record, horizon) for record in entered_records]
    known_returns = [value for value in returns if value is not None]
    wins = [value for value in known_returns if value > 0]
    return {
        "record_count": len(records),
        "horizon": horizon,
        "trade_count": len(entered_records),
        "trades_with_future_return": len(known_returns),
        "win_rate": (len(wins) / len(known_returns)) if known_returns else None,
        "average_return_pct": (sum(known_returns) / len(known_returns)) if known_returns else None,
        "max_consecutive_losses": max_consecutive_losses(records, horizon),
        "decision_groups": analysis.summarize(records)["decision_groups"],
        "group_opportunity": group_opportunity(records, horizon),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay recorded signal decisions without touching bot state")
    parser.add_argument("record_file", nargs="?", default="signal_records.jsonl")
    parser.add_argument("--horizon", choices=analysis.FUTURE_KEYS, default="1h")
    args = parser.parse_args()
    records = analysis.read_records(Path(args.record_file))
    print(json.dumps(replay(records, args.horizon), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
