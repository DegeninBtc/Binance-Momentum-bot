#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analyze_signal_records as analysis


DEFAULT_SPLITS = (0.6, 0.2, 0.2)


def parse_time(value: Any) -> datetime:
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def sorted_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: parse_time(item.get("recorded_at") or item.get("checked_at")))


def split_counts(total: int, ratios: tuple[float, float, float] = DEFAULT_SPLITS) -> tuple[int, int, int]:
    if total <= 0:
        return (0, 0, 0)
    train = int(total * ratios[0])
    validate = int(total * ratios[1])
    if total >= 3:
        train = max(1, train)
        validate = max(1, validate)
    test = total - train - validate
    if total >= 3 and test <= 0:
        test = 1
        if train >= validate and train > 1:
            train -= 1
        elif validate > 1:
            validate -= 1
    if train + validate + test > total:
        test = max(0, total - train - validate)
    return (train, validate, test)


def phase_summary(name: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = analysis.summarize(records)
    entered_records = [record for record in records if analysis.entered(record)]
    return {
        "phase": name,
        "record_count": summary["record_count"],
        "entered_count": summary["entered_count"],
        "skipped_count": summary["skipped_count"],
        "decision_groups": summary["decision_groups"],
        "future_returns": summary["future_returns"],
        "entered_future_returns": analysis.return_summary(entered_records),
    }


def walk_forward(records: list[dict[str, Any]], ratios: tuple[float, float, float] = DEFAULT_SPLITS) -> dict[str, Any]:
    ordered = sorted_records(records)
    train_count, validate_count, test_count = split_counts(len(ordered), ratios)
    train = ordered[:train_count]
    validate = ordered[train_count : train_count + validate_count]
    test = ordered[train_count + validate_count : train_count + validate_count + test_count]
    return {
        "record_count": len(ordered),
        "split_ratio": {"train": ratios[0], "validation": ratios[1], "test": ratios[2]},
        "split_count": {"train": len(train), "validation": len(validate), "test": len(test)},
        "phases": {
            "train": phase_summary("train", train),
            "validation": phase_summary("validation", validate),
            "test": phase_summary("test", test),
        },
    }


def parse_ratio(value: str) -> tuple[float, float, float]:
    parts = [float(item.strip()) for item in value.split(",") if item.strip()]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("split ratio must contain train,validation,test")
    total = sum(parts)
    if total <= 0:
        raise argparse.ArgumentTypeError("split ratio total must be greater than zero")
    return (parts[0] / total, parts[1] / total, parts[2] / total)


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward validation summary for signal_records.jsonl")
    parser.add_argument("record_file", nargs="?", default="signal_records.jsonl")
    parser.add_argument("--split", default="60,20,20", type=parse_ratio, help="train,validation,test ratio; default 60,20,20")
    args = parser.parse_args()
    records = analysis.read_records(Path(args.record_file))
    print(json.dumps(walk_forward(records, args.split), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
