#!/usr/bin/env python3
"""Calculate estimated costs for thesis agent chat token summaries."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_MODEL = "gpt-5.5"
DEFAULT_INPUT_RATE = 5.0
DEFAULT_CACHED_INPUT_RATE = 0.5
DEFAULT_OUTPUT_RATE = 30.0
PER_MILLION = 1_000_000.0

DETAILS_CSV = "agent_chat_costs.csv"
STATS_CSV = "agent_chat_cost_statistics.csv"
MARKDOWN_REPORT = "agent_chat_costs.md"

REQUIRED_TOKEN_FIELDS = (
    "chat_id",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)

DETAIL_FIELDS = [
    "thesis_id",
    "chat_id",
    "token_file",
    "model",
    "long_prompt_pricing",
    "input_rate_usd_per_1m",
    "cached_input_rate_usd_per_1m",
    "output_rate_usd_per_1m",
    "effective_input_rate_usd_per_1m",
    "effective_cached_input_rate_usd_per_1m",
    "effective_output_rate_usd_per_1m",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "input_cost_usd",
    "cached_input_cost_usd",
    "cache_write_input_cost_usd",
    "output_cost_usd",
    "total_cost_usd",
    "cache_ratio",
    "reasoning_output_share",
    "effective_cost_per_1m_total_tokens_usd",
]

STAT_FIELDS = [
    "statistic",
    "chat_count",
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "input_cost_usd",
    "cached_input_cost_usd",
    "cache_write_input_cost_usd",
    "output_cost_usd",
    "total_cost_usd",
    "cache_ratio",
    "reasoning_output_share",
    "effective_cost_per_1m_total_tokens_usd",
]

SUM_FIELDS = {
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
    "input_cost_usd",
    "cached_input_cost_usd",
    "cache_write_input_cost_usd",
    "output_cost_usd",
    "total_cost_usd",
}


@dataclass(frozen=True)
class PriceConfig:
    model: str
    input_rate: float
    cached_input_rate: float
    output_rate: float
    long_prompt_pricing: bool

    @property
    def effective_input_rate(self) -> float:
        if self.long_prompt_pricing:
            return self.input_rate * 2.0
        return self.input_rate

    @property
    def effective_cached_input_rate(self) -> float:
        if self.long_prompt_pricing:
            return self.cached_input_rate * 2.0
        return self.cached_input_rate

    @property
    def effective_output_rate(self) -> float:
        if self.long_prompt_pricing:
            return self.output_rate * 1.5
        return self.output_rate


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("rates must be non-negative")
    return parsed


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise TypeError(f"{path} must contain a JSON object")
    return payload


def required_int(payload: dict[str, Any], key: str, path: Path) -> int:
    if key not in payload:
        raise KeyError(f"{path} is missing required field: {key}")
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{path}.{key} must be an integer")
    if value < 0:
        raise ValueError(f"{path}.{key} must be non-negative")
    return value


def required_str(payload: dict[str, Any], key: str, path: Path) -> str:
    if key not in payload:
        raise KeyError(f"{path} is missing required field: {key}")
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{path}.{key} must be a non-empty string")
    return value


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def discover_token_files(root: Path) -> list[Path]:
    token_files = sorted(root.glob("thesis_*/agent_e2e/agent_tokens.json"))
    if not token_files:
        raise FileNotFoundError(
            f"No thesis_*/agent_e2e/agent_tokens.json files found under {root}"
        )
    return token_files


def calculate_row(path: Path, root: Path, prices: PriceConfig) -> dict[str, Any]:
    payload = load_json(path)
    for field in REQUIRED_TOKEN_FIELDS:
        if field not in payload:
            raise KeyError(f"{path} is missing required field: {field}")

    input_tokens = required_int(payload, "input_tokens", path)
    cached_input_tokens = required_int(payload, "cached_input_tokens", path)
    cache_write_input_tokens = required_int(payload, "cache_write_input_tokens", path)
    output_tokens = required_int(payload, "output_tokens", path)
    reasoning_output_tokens = required_int(payload, "reasoning_output_tokens", path)
    total_tokens = required_int(payload, "total_tokens", path)

    uncached_input_tokens = (
        input_tokens - cached_input_tokens - cache_write_input_tokens
    )
    if uncached_input_tokens < 0:
        raise ValueError(
            f"{path} has cached/cache-write input tokens greater than input_tokens"
        )
    if reasoning_output_tokens > output_tokens:
        raise ValueError(f"{path} has reasoning_output_tokens greater than output_tokens")

    input_cost = (
        uncached_input_tokens / PER_MILLION * prices.effective_input_rate
    )
    cache_write_cost = (
        cache_write_input_tokens / PER_MILLION * prices.effective_input_rate
    )
    cached_cost = (
        cached_input_tokens / PER_MILLION * prices.effective_cached_input_rate
    )
    output_cost = output_tokens / PER_MILLION * prices.effective_output_rate
    total_cost = input_cost + cache_write_cost + cached_cost + output_cost

    thesis_id = path.parent.parent.name
    return {
        "thesis_id": thesis_id,
        "chat_id": required_str(payload, "chat_id", path),
        "token_file": str(path.relative_to(root)),
        "model": prices.model,
        "long_prompt_pricing": prices.long_prompt_pricing,
        "input_rate_usd_per_1m": prices.input_rate,
        "cached_input_rate_usd_per_1m": prices.cached_input_rate,
        "output_rate_usd_per_1m": prices.output_rate,
        "effective_input_rate_usd_per_1m": prices.effective_input_rate,
        "effective_cached_input_rate_usd_per_1m": prices.effective_cached_input_rate,
        "effective_output_rate_usd_per_1m": prices.effective_output_rate,
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "cache_write_input_tokens": cache_write_input_tokens,
        "uncached_input_tokens": uncached_input_tokens,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_tokens": total_tokens,
        "input_cost_usd": input_cost,
        "cached_input_cost_usd": cached_cost,
        "cache_write_input_cost_usd": cache_write_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_cost,
        "cache_ratio": safe_divide(cached_input_tokens, input_tokens),
        "reasoning_output_share": safe_divide(reasoning_output_tokens, output_tokens),
        "effective_cost_per_1m_total_tokens_usd": safe_divide(
            total_cost * PER_MILLION,
            total_tokens,
        ),
    }


def calculate_rows(root: Path, prices: PriceConfig) -> list[dict[str, Any]]:
    return [calculate_row(path, root, prices) for path in discover_token_files(root)]


def aggregate_total(rows: list[dict[str, Any]]) -> dict[str, Any]:
    row: dict[str, Any] = {"statistic": "total", "chat_count": len(rows)}
    for field in SUM_FIELDS:
        row[field] = sum(float(item[field]) for item in rows)
    row["cache_ratio"] = safe_divide(
        row["cached_input_tokens"],
        row["input_tokens"],
    )
    row["reasoning_output_share"] = safe_divide(
        row["reasoning_output_tokens"],
        row["output_tokens"],
    )
    row["effective_cost_per_1m_total_tokens_usd"] = safe_divide(
        row["total_cost_usd"] * PER_MILLION,
        row["total_tokens"],
    )
    return row


def aggregate_stat(
    rows: list[dict[str, Any]],
    name: str,
    reducer: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {"statistic": name, "chat_count": len(rows)}
    for field in STAT_FIELDS:
        if field in {"statistic", "chat_count"}:
            continue
        values = [float(item[field]) for item in rows]
        row[field] = reducer(values)
    return row


def calculate_statistics(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        aggregate_total(rows),
        aggregate_stat(rows, "mean", mean),
        aggregate_stat(rows, "median", median),
        aggregate_stat(rows, "min", min),
        aggregate_stat(rows, "max", max),
    ]


def csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        return f"{value:.10f}".rstrip("0").rstrip(".")
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fieldnames})


def format_int(value: float | int) -> str:
    return f"{int(round(float(value))):,}"


def format_usd(value: float | int) -> str:
    return f"${float(value):,.4f}"


def format_rate(value: float | int) -> str:
    return f"${float(value):,.2f}"


def format_percent(value: float | int) -> str:
    return f"{float(value) * 100:.2f}%"


def markdown_table(
    headers: list[str],
    rows: list[list[str]],
    right_aligned_columns: set[int] | None = None,
) -> str:
    right_aligned_columns = right_aligned_columns or set()
    separator = [
        "---:" if index in right_aligned_columns else "---"
        for index, _ in enumerate(headers)
    ]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def markdown_report(
    rows: list[dict[str, Any]],
    statistics: list[dict[str, Any]],
    prices: PriceConfig,
) -> str:
    detail_rows = [
        [
            row["thesis_id"],
            row["chat_id"],
            format_int(row["input_tokens"]),
            format_int(row["cached_input_tokens"]),
            format_int(row["output_tokens"]),
            format_int(row["reasoning_output_tokens"]),
            format_int(row["total_tokens"]),
            format_usd(row["total_cost_usd"]),
            format_percent(row["cache_ratio"]),
            format_percent(row["reasoning_output_share"]),
            format_rate(row["effective_cost_per_1m_total_tokens_usd"]),
        ]
        for row in rows
    ]
    stat_rows = [
        [
            row["statistic"].title(),
            format_int(row["chat_count"]),
            format_int(row["input_tokens"]),
            format_int(row["cached_input_tokens"]),
            format_int(row["output_tokens"]),
            format_int(row["total_tokens"]),
            format_usd(row["total_cost_usd"]),
            format_percent(row["cache_ratio"]),
            format_percent(row["reasoning_output_share"]),
            format_rate(row["effective_cost_per_1m_total_tokens_usd"]),
        ]
        for row in statistics
    ]

    pricing_rows = [
        ["Model", prices.model],
        ["Input rate per 1M tokens", format_rate(prices.input_rate)],
        ["Cached input rate per 1M tokens", format_rate(prices.cached_input_rate)],
        ["Output rate per 1M tokens", format_rate(prices.output_rate)],
        ["Long prompt pricing", "enabled" if prices.long_prompt_pricing else "disabled"],
        ["Effective input rate per 1M tokens", format_rate(prices.effective_input_rate)],
        [
            "Effective cached input rate per 1M tokens",
            format_rate(prices.effective_cached_input_rate),
        ],
        ["Effective output rate per 1M tokens", format_rate(prices.effective_output_rate)],
    ]

    return "\n\n".join(
        [
            "# Agent Chat Cost Tables",
            (
                "Estimated from thesis agent token summaries. Reasoning tokens are "
                "reported separately, but are included in output tokens and are not "
                "billed a second time."
            ),
            "## Pricing Configuration",
            markdown_table(["Setting", "Value"], pricing_rows, {1}),
            "## Per-Chat Costs",
            markdown_table(
                [
                    "Thesis",
                    "Chat ID",
                    "Input",
                    "Cached Input",
                    "Output",
                    "Reasoning",
                    "Total Tokens",
                    "Cost",
                    "Cache Ratio",
                    "Reasoning Share",
                    "Effective $/1M",
                ],
                detail_rows,
                set(range(2, 11)),
            ),
            "## Summary Statistics",
            markdown_table(
                [
                    "Statistic",
                    "Chats",
                    "Input",
                    "Cached Input",
                    "Output",
                    "Total Tokens",
                    "Cost",
                    "Cache Ratio",
                    "Reasoning Share",
                    "Effective $/1M",
                ],
                stat_rows,
                set(range(1, 10)),
            ),
        ]
    ) + "\n"


def write_outputs(
    root: Path,
    rows: list[dict[str, Any]],
    statistics: list[dict[str, Any]],
    prices: PriceConfig,
) -> list[Path]:
    details_path = root / DETAILS_CSV
    stats_path = root / STATS_CSV
    markdown_path = root / MARKDOWN_REPORT

    write_csv(details_path, rows, DETAIL_FIELDS)
    write_csv(stats_path, statistics, STAT_FIELDS)
    markdown_path.write_text(
        markdown_report(rows, statistics, prices),
        encoding="utf-8",
    )
    return [details_path, stats_path, markdown_path]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Calculate estimated costs for thesis agent chat token files.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Directory containing thesis_* subdirectories. Defaults to the current directory.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model label to write into outputs. Defaults to {DEFAULT_MODEL}.",
    )
    parser.add_argument(
        "--input-rate",
        type=positive_float,
        default=DEFAULT_INPUT_RATE,
        help="USD per 1M uncached input tokens.",
    )
    parser.add_argument(
        "--cached-input-rate",
        type=positive_float,
        default=DEFAULT_CACHED_INPUT_RATE,
        help="USD per 1M cached input tokens.",
    )
    parser.add_argument(
        "--output-rate",
        type=positive_float,
        default=DEFAULT_OUTPUT_RATE,
        help="USD per 1M output tokens.",
    )
    parser.add_argument(
        "--long-prompt-pricing",
        action="store_true",
        help=(
            "Apply GPT-5.5 long-prompt multipliers: 2x input token rates "
            "and 1.5x output token rates."
        ),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    root = args.root.resolve()
    prices = PriceConfig(
        model=args.model,
        input_rate=args.input_rate,
        cached_input_rate=args.cached_input_rate,
        output_rate=args.output_rate,
        long_prompt_pricing=args.long_prompt_pricing,
    )
    rows = calculate_rows(root, prices)
    statistics = calculate_statistics(rows)
    outputs = write_outputs(root, rows, statistics, prices)

    print(f"Processed {len(rows)} agent chat token files.")
    for path in outputs:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
