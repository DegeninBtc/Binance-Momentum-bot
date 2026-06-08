#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path
from typing import Any


FUTURE_KEYS = ("5m", "15m", "1h", "4h")
CSV_COLUMNS = (
    "recorded_at",
    "source",
    "symbol",
    "decision_group",
    "final_action",
    "entered",
    "square_score",
    "entry_reason",
    "return_5m_pct",
    "return_15m_pct",
    "return_1h_pct",
    "return_4h_pct",
)


def read_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def entered(record: dict[str, Any]) -> bool:
    return bool(record.get("entered") or record.get("final_action") == "entered")


def entry_confirmation(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("entry_confirmation") or {}
    return value if isinstance(value, dict) else {}


def checks(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("checks") or entry_confirmation(record).get("checks") or {}
    return value if isinstance(value, dict) else {}


def check_failed(record: dict[str, Any], key: str) -> bool:
    value = checks(record).get(key)
    return isinstance(value, dict) and value.get("passed") is False


def square_low_confidence(record: dict[str, Any]) -> bool:
    score = number((record.get("square_confidence") or {}).get("score"))
    threshold = number(record.get("min_square_confidence_score")) or 35
    return score is not None and score < threshold


def account_risk_blocked(record: dict[str, Any]) -> bool:
    risk = record.get("account_risk") or {}
    if isinstance(risk, dict) and risk.get("entry_blocked"):
        return True
    reason = decision_reason(record).lower()
    return "account risk" in reason or "exposure" in reason or "drawdown" in reason or "consecutive losses" in reason


def decision_reason(record: dict[str, Any]) -> str:
    return str(entry_confirmation(record).get("reason") or record.get("note") or "")


def decision_group(record: dict[str, Any]) -> str:
    if entered(record):
        return "entered"
    if square_low_confidence(record):
        return "square_low_confidence"
    if check_failed(record, "kline"):
        return "kline_rejected"
    if check_failed(record, "liquidity"):
        return "orderbook_rejected"
    if account_risk_blocked(record):
        return "account_risk_rejected"
    if entry_confirmation(record).get("passed") is False:
        return "entry_rejected"
    return "skipped_other"


def future_return(record: dict[str, Any], key: str) -> float | None:
    future_returns = record.get("future_returns") or {}
    if not isinstance(future_returns, dict):
        return None
    item = future_returns.get(key)
    return number(item.get("return_pct") if isinstance(item, dict) else item)


def return_summary(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for key in FUTURE_KEYS:
        values = [value for value in (future_return(record, key) for record in records) if value is not None]
        positives = [value for value in values if value > 0]
        result[key] = {
            "count": len(values),
            "mean_pct": statistics.fmean(values) if values else None,
            "median_pct": statistics.median(values) if values else None,
            "positive_rate": (len(positives) / len(values)) if values else None,
        }
    return result


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    entered_records = [record for record in records if entered(record)]
    skipped_records = [record for record in records if not entered(record)]
    groups: dict[str, int] = {}
    for record in records:
        group = decision_group(record)
        groups[group] = groups.get(group, 0) + 1
    return {
        "record_count": len(records),
        "entered_count": len(entered_records),
        "skipped_count": len(skipped_records),
        "square_low_confidence_count": groups.get("square_low_confidence", 0),
        "kline_block_count": sum(1 for record in records if check_failed(record, "kline")),
        "orderbook_block_count": sum(1 for record in records if check_failed(record, "liquidity")),
        "account_risk_block_count": sum(1 for record in records if account_risk_blocked(record)),
        "decision_groups": groups,
        "future_returns": return_summary(records),
        "future_returns_by_decision": {
            "entered": return_summary(entered_records),
            "skipped": return_summary(skipped_records),
        },
    }


def record_to_csv_row(record: dict[str, Any]) -> dict[str, Any]:
    candidate = record.get("candidate") or {}
    symbol = candidate.get("symbol") if isinstance(candidate, dict) else ""
    return {
        "recorded_at": record.get("recorded_at", ""),
        "source": record.get("source", ""),
        "symbol": symbol or "",
        "decision_group": decision_group(record),
        "final_action": record.get("final_action", ""),
        "entered": entered(record),
        "square_score": (record.get("square_confidence") or {}).get("score", ""),
        "entry_reason": decision_reason(record),
        "return_5m_pct": future_return(record, "5m"),
        "return_15m_pct": future_return(record, "15m"),
        "return_1h_pct": future_return(record, "1h"),
        "return_4h_pct": future_return(record, "4h"),
    }


def write_csv(records: list[dict[str, Any]], output: Path | None) -> None:
    handle = output.open("w", encoding="utf-8", newline="") if output else sys.stdout
    close_handle = output is not None
    try:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            writer.writerow(record_to_csv_row(record))
    finally:
        if close_handle:
            handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Binance Momentum Bot signal_records.jsonl")
    parser.add_argument("record_file", nargs="?", default="signal_records.jsonl")
    parser.add_argument("--csv", action="store_true", help="write per-record CSV instead of JSON summary")
    parser.add_argument("--output", help="CSV output path; defaults to stdout")
    args = parser.parse_args()
    records = read_records(Path(args.record_file))
    if args.csv:
        write_csv(records, Path(args.output) if args.output else None)
    else:
        print(json.dumps(summarize(records), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
