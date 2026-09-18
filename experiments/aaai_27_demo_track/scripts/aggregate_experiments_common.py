#!/usr/bin/env python3
"""Aggregate thesis experiment results into paper tables."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parent

CLAIMS_EVALUATION = "claims_evaluation_result.json"
AGENT_CLAIMS_EVALUATION = "agent_claims_evaluation_result.json"
CLAIMS_CODE_EVALUATION = "claims_code_evaluation_result.json"
E2E_RESULTS = "e2e_results.json"
REQUIRED_FILES = (
    CLAIMS_EVALUATION,
    AGENT_CLAIMS_EVALUATION,
    CLAIMS_CODE_EVALUATION,
    E2E_RESULTS,
)

EXTRACTION_METRICS = (
    ("source_precision", "Source Precision", "higher", "score"),
    ("source_recall", "Source Recall", "higher", "score"),
    ("source_f1", "Source F1", "higher", "score"),
    ("decomposition_precision", "Decomposition Precision", "higher", "score"),
    ("decomposition_recall", "Decomposition Recall", "higher", "score"),
    ("decomposition_f1", "Decomposition F1", "higher", "score"),
    ("gold_full_semantic_coverage", "Full semantic coverage", "higher", "score"),
    (
        "gold_partial_or_better_coverage",
        "Partial-or-better coverage",
        "higher",
        "score",
    ),
    ("over_decomposition_rate", "Over-decomposition rate", "lower", "score"),
    ("under_decomposition_rate", "Under-decomposition rate", "lower", "score"),
)

VERIFICATION_METRICS = (
    ("accuracy", "Accuracy", "higher", "score"),
    ("macro_f1", "Macro-F1", "higher", "score"),
    ("balanced_accuracy", "Balanced Accuracy", "higher", "score"),
    ("precision_implemented", "IMPLEMENTED Precision", "higher", "score"),
    ("recall_implemented", "IMPLEMENTED Recall", "higher", "score"),
    ("f1_implemented", "IMPLEMENTED F1", "higher", "score"),
    ("false_implementation_rate", "False Implementation Rate", "lower", "score"),
)

E2E_METRICS = (
    (
        "implementation_rate_mae_pp",
        "Implementation-rate MAE, pp",
        "lower",
        "pp",
    ),
    ("median_absolute_error_pp", "Median absolute error, pp", "lower", "pp"),
    ("mean_signed_error_pp", "Mean signed error, pp", "signed", "pp"),
    ("weighted_score_mae_pp", "Weighted-score MAE, pp", "lower", "pp"),
    ("spearman_rho", "Spearman rho", "higher", "score"),
    ("mean_claim_count_deviation", "Mean claim-count deviation", "lower", "score"),
    ("mean_extracted_claims", "Mean extracted claims", "neutral", "count"),
)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def discover_thesis_dirs(root: Path = ROOT) -> list[Path]:
    thesis_dirs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and path.name.startswith("thesis_")
    )
    if not thesis_dirs:
        raise FileNotFoundError(f"No thesis_* directories found in {root}")
    return thesis_dirs


def require_inputs(thesis_dir: Path) -> None:
    missing = [name for name in REQUIRED_FILES if not (thesis_dir / name).is_file()]
    if missing:
        missing_list = ", ".join(missing)
        raise FileNotFoundError(f"{thesis_dir.name} is missing: {missing_list}")


def required_number(mapping: dict[str, Any], key: str, context: str) -> float:
    if key not in mapping:
        raise KeyError(f"{context} missing numeric field: {key}")
    value = mapping[key]
    if not isinstance(value, (int, float)):
        raise TypeError(f"{context}.{key} must be numeric, got {type(value).__name__}")
    return float(value)


def safe_divide(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def extraction_metrics(payload: dict[str, Any], context: str) -> dict[str, float]:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise TypeError(f"{context}.metrics must be an object")

    row = {
        key: required_number(metrics, key, context)
        for key, _, _, _ in EXTRACTION_METRICS
        if key not in {"over_decomposition_rate", "under_decomposition_rate"}
    }
    denominator = required_number(
        metrics,
        "decomposition_gold_claims_in_found_sources",
        context,
    )
    row["over_decomposition_rate"] = safe_divide(
        required_number(metrics, "over_decomposition_count", context),
        denominator,
    )
    row["under_decomposition_rate"] = safe_divide(
        required_number(metrics, "gold_claims_affected_by_under_decomposition", context),
        denominator,
    )
    row["gold_total"] = required_number(metrics, "gold_total", context)
    row["extracted_total"] = required_number(metrics, "osa_total", context)
    return row


def verification_metrics(payload: dict[str, Any], context: str) -> dict[str, float]:
    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        raise TypeError(f"{context}.metrics must be an object")
    return {
        key: required_number(metrics, key, context)
        for key, _, _, _ in VERIFICATION_METRICS
    } | {
        "included_total": required_number(metrics, "included_total", context),
        "excluded_total": required_number(metrics, "excluded_total", context),
    }


def e2e_system_metrics(
    gold: dict[str, Any],
    system: dict[str, Any],
    context: str,
) -> dict[str, float]:
    gold_rate = required_number(gold, "implementation_rate", f"{context}.gold")
    system_rate = required_number(system, "implementation_rate", context)
    gold_weighted = required_number(gold, "weighted_score", f"{context}.gold")
    system_weighted = required_number(system, "weighted_score", context)
    gold_claims = required_number(gold, "claims_total", f"{context}.gold")
    system_claims = required_number(system, "claims_total", context)
    return {
        "implementation_rate": system_rate,
        "weighted_score": system_weighted,
        "absolute_error_pp": abs(system_rate - gold_rate) * 100.0,
        "signed_error_pp": (system_rate - gold_rate) * 100.0,
        "weighted_absolute_error_pp": abs(system_weighted - gold_weighted) * 100.0,
        "claim_count_deviation": safe_divide(abs(system_claims - gold_claims), gold_claims),
        "claims_total": system_claims,
    }


def e2e_metrics(payload: dict[str, Any], context: str) -> dict[str, Any]:
    for key in ("gold", "osa", "agent"):
        if not isinstance(payload.get(key), dict):
            raise TypeError(f"{context}.{key} must be an object")
    gold = payload["gold"]
    return {
        "gold": {
            "implementation_rate": required_number(
                gold,
                "implementation_rate",
                f"{context}.gold",
            ),
            "weighted_score": required_number(gold, "weighted_score", f"{context}.gold"),
            "claims_total": required_number(gold, "claims_total", f"{context}.gold"),
        },
        "osa": e2e_system_metrics(gold, payload["osa"], f"{context}.osa"),
        "agent": e2e_system_metrics(gold, payload["agent"], f"{context}.agent"),
    }


def load_records(root: Path = ROOT) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for thesis_dir in discover_thesis_dirs(root):
        require_inputs(thesis_dir)
        thesis_id = thesis_dir.name
        records.append(
            {
                "thesis_id": thesis_id,
                "extraction": {
                    "osa": extraction_metrics(
                        load_json(thesis_dir / CLAIMS_EVALUATION),
                        f"{thesis_id}.{CLAIMS_EVALUATION}",
                    ),
                    "agent": extraction_metrics(
                        load_json(thesis_dir / AGENT_CLAIMS_EVALUATION),
                        f"{thesis_id}.{AGENT_CLAIMS_EVALUATION}",
                    ),
                },
                "verification": verification_metrics(
                    load_json(thesis_dir / CLAIMS_CODE_EVALUATION),
                    f"{thesis_id}.{CLAIMS_CODE_EVALUATION}",
                ),
                "e2e": e2e_metrics(
                    load_json(thesis_dir / E2E_RESULTS),
                    f"{thesis_id}.{E2E_RESULTS}",
                ),
            }
        )
    return records


def mean(values: list[float]) -> float:
    if not values:
        return math.nan
    return sum(values) / len(values)


def average_ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        average_rank = (index + 1 + end) / 2.0
        for original_index, _ in indexed[index:end]:
            ranks[original_index] = average_rank
        index = end
    return ranks


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys):
        raise ValueError("Pearson inputs must have the same length")
    if not xs:
        return math.nan
    x_mean = mean(xs)
    y_mean = mean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_denom = math.sqrt(sum((x - x_mean) ** 2 for x in xs))
    y_denom = math.sqrt(sum((y - y_mean) ** 2 for y in ys))
    if x_denom == 0 or y_denom == 0:
        return math.nan
    return numerator / (x_denom * y_denom)


def spearman(xs: list[float], ys: list[float]) -> float:
    return pearson(average_ranks(xs), average_ranks(ys))


def aggregate_extraction(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for key, label, direction, kind in EXTRACTION_METRICS:
        rows.append(
            {
                "metric_key": key,
                "metric": label,
                "direction": direction,
                "kind": kind,
                "osa_edu": mean(
                    [record["extraction"]["osa"][key] for record in records]
                ),
                "agent": mean(
                    [record["extraction"]["agent"][key] for record in records]
                ),
            }
        )
    return rows


def aggregate_verification(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for key, label, direction, kind in VERIFICATION_METRICS:
        rows.append(
            {
                "metric_key": key,
                "metric": label,
                "direction": direction,
                "kind": kind,
                "osa_edu": mean([record["verification"][key] for record in records]),
            }
        )
    return rows


def aggregate_e2e(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gold_rates = [
        record["e2e"]["gold"]["implementation_rate"]
        for record in records
    ]
    system_rows: dict[str, dict[str, float]] = {}
    for system in ("osa", "agent"):
        system_rates = [
            record["e2e"][system]["implementation_rate"]
            for record in records
        ]
        absolute_errors = [
            record["e2e"][system]["absolute_error_pp"]
            for record in records
        ]
        system_rows[system] = {
            "implementation_rate_mae_pp": mean(absolute_errors),
            "median_absolute_error_pp": median(absolute_errors),
            "mean_signed_error_pp": mean(
                [record["e2e"][system]["signed_error_pp"] for record in records]
            ),
            "weighted_score_mae_pp": mean(
                [
                    record["e2e"][system]["weighted_absolute_error_pp"]
                    for record in records
                ]
            ),
            "spearman_rho": spearman(gold_rates, system_rates),
            "mean_claim_count_deviation": mean(
                [
                    record["e2e"][system]["claim_count_deviation"]
                    for record in records
                ]
            ),
            "mean_extracted_claims": mean(
                [record["e2e"][system]["claims_total"] for record in records]
            ),
        }

    rows = []
    for key, label, direction, kind in E2E_METRICS:
        rows.append(
            {
                "metric_key": key,
                "metric": label,
                "direction": direction,
                "kind": kind,
                "osa_edu": system_rows["osa"][key],
                "agent": system_rows["agent"][key],
            }
        )
    return rows


def aggregate_tables(records: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    return {
        "extraction": aggregate_extraction(records),
        "verification": aggregate_verification(records),
        "e2e": aggregate_e2e(records),
    }


def csv_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    return value


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fieldnames})


def format_number(value: float, kind: str) -> str:
    if isinstance(value, float) and math.isnan(value):
        return "nan"
    if kind == "score":
        return f"{value:.3f}"
    if kind in {"pp", "count"}:
        return f"{value:.2f}"
    return str(value)


def metric_label(metric: str, direction: str) -> str:
    if direction == "higher":
        return f"{metric} (higher)"
    if direction == "lower":
        return f"{metric} (lower)"
    return metric


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


def aggregate_markdown(tables: dict[str, list[dict[str, Any]]], thesis_count: int) -> str:
    extraction_rows = [
        [
            metric_label(row["metric"], row["direction"]),
            format_number(row["osa_edu"], row["kind"]),
            format_number(row["agent"], row["kind"]),
        ]
        for row in tables["extraction"]
    ]
    verification_rows = [
        [
            metric_label(row["metric"], row["direction"]),
            format_number(row["osa_edu"], row["kind"]),
        ]
        for row in tables["verification"]
    ]
    e2e_rows = [
        [
            metric_label(row["metric"], row["direction"]),
            format_number(row["osa_edu"], row["kind"]),
            format_number(row["agent"], row["kind"]),
        ]
        for row in tables["e2e"]
    ]

    return "\n\n".join(
        [
            "# Aggregate Experiment Tables",
            f"Macro-averaged across {thesis_count} theses.",
            "## Experiment 1 - Claim Extraction",
            markdown_table(
                ["Metric", "OSA.Edu", "Agent"],
                extraction_rows,
                {1, 2},
            ),
            "## Experiment 2 - Verification On Gold Claims",
            markdown_table(["Metric", "OSA.Edu"], verification_rows, {1}),
            "## Experiment 3 - End-to-End",
            markdown_table(["Metric", "OSA.Edu", "Agent"], e2e_rows, {1, 2}),
        ]
    ) + "\n"


def appendices_markdown(records: list[dict[str, Any]]) -> str:
    extraction_rows = []
    verification_rows = []
    e2e_rows = []
    for record in records:
        thesis_id = record["thesis_id"]
        extraction_rows.append(
            [
                thesis_id,
                format_number(record["extraction"]["osa"]["source_f1"], "score"),
                format_number(record["extraction"]["agent"]["source_f1"], "score"),
                format_number(record["extraction"]["osa"]["decomposition_f1"], "score"),
                format_number(record["extraction"]["agent"]["decomposition_f1"], "score"),
                format_number(
                    record["extraction"]["osa"]["gold_full_semantic_coverage"],
                    "score",
                ),
                format_number(
                    record["extraction"]["agent"]["gold_full_semantic_coverage"],
                    "score",
                ),
                format_number(
                    record["extraction"]["osa"]["over_decomposition_rate"],
                    "score",
                ),
                format_number(
                    record["extraction"]["agent"]["over_decomposition_rate"],
                    "score",
                ),
                format_number(
                    record["extraction"]["osa"]["under_decomposition_rate"],
                    "score",
                ),
                format_number(
                    record["extraction"]["agent"]["under_decomposition_rate"],
                    "score",
                ),
            ]
        )
        verification_rows.append(
            [
                thesis_id,
                format_number(record["verification"]["accuracy"], "score"),
                format_number(record["verification"]["macro_f1"], "score"),
                format_number(record["verification"]["balanced_accuracy"], "score"),
                format_number(
                    record["verification"]["precision_implemented"],
                    "score",
                ),
                format_number(record["verification"]["recall_implemented"], "score"),
                format_number(record["verification"]["f1_implemented"], "score"),
                format_number(
                    record["verification"]["false_implementation_rate"],
                    "score",
                ),
            ]
        )
        e2e_rows.append(
            [
                thesis_id,
                format_number(record["e2e"]["gold"]["implementation_rate"], "score"),
                format_number(record["e2e"]["osa"]["implementation_rate"], "score"),
                format_number(record["e2e"]["agent"]["implementation_rate"], "score"),
                format_number(record["e2e"]["osa"]["absolute_error_pp"], "pp"),
                format_number(record["e2e"]["agent"]["absolute_error_pp"], "pp"),
                format_number(record["e2e"]["osa"]["signed_error_pp"], "pp"),
                format_number(record["e2e"]["agent"]["signed_error_pp"], "pp"),
                format_number(
                    record["e2e"]["osa"]["weighted_absolute_error_pp"],
                    "pp",
                ),
                format_number(
                    record["e2e"]["agent"]["weighted_absolute_error_pp"],
                    "pp",
                ),
                format_number(
                    record["e2e"]["osa"]["claim_count_deviation"],
                    "score",
                ),
                format_number(
                    record["e2e"]["agent"]["claim_count_deviation"],
                    "score",
                ),
            ]
        )

    return "\n\n".join(
        [
            "## Appendix - Claim Extraction By Thesis",
            markdown_table(
                [
                    "Thesis",
                    "OSA Source F1",
                    "Agent Source F1",
                    "OSA Decomp F1",
                    "Agent Decomp F1",
                    "OSA Full Cov",
                    "Agent Full Cov",
                    "OSA Over Rate",
                    "Agent Over Rate",
                    "OSA Under Rate",
                    "Agent Under Rate",
                ],
                extraction_rows,
                set(range(1, 11)),
            ),
            "## Appendix - Verification By Thesis",
            markdown_table(
                [
                    "Thesis",
                    "Accuracy",
                    "Macro-F1",
                    "Balanced Acc",
                    "Impl Precision",
                    "Impl Recall",
                    "Impl F1",
                    "False Impl Rate",
                ],
                verification_rows,
                set(range(1, 8)),
            ),
            "## Appendix - End-to-End By Thesis",
            markdown_table(
                [
                    "Thesis",
                    "Gold Rate",
                    "OSA Rate",
                    "Agent Rate",
                    "OSA Abs Err pp",
                    "Agent Abs Err pp",
                    "OSA Signed pp",
                    "Agent Signed pp",
                    "OSA Weighted Err pp",
                    "Agent Weighted Err pp",
                    "OSA Count Dev",
                    "Agent Count Dev",
                ],
                e2e_rows,
                set(range(1, 12)),
            ),
        ]
    ) + "\n"


def extraction_appendix_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        row: dict[str, Any] = {"thesis_id": record["thesis_id"]}
        for system_key, prefix in (("osa", "osa_edu"), ("agent", "agent")):
            metrics = record["extraction"][system_key]
            for key, _, _, _ in EXTRACTION_METRICS:
                row[f"{prefix}_{key}"] = metrics[key]
            row[f"{prefix}_gold_total"] = metrics["gold_total"]
            row[f"{prefix}_extracted_total"] = metrics["extracted_total"]
        rows.append(row)
    return rows


def verification_appendix_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        row = {"thesis_id": record["thesis_id"]}
        for key, _, _, _ in VERIFICATION_METRICS:
            row[key] = record["verification"][key]
        row["included_total"] = record["verification"]["included_total"]
        row["excluded_total"] = record["verification"]["excluded_total"]
        rows.append(row)
    return rows


def e2e_appendix_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        row = {
            "thesis_id": record["thesis_id"],
            "gold_implementation_rate": record["e2e"]["gold"]["implementation_rate"],
            "gold_weighted_score": record["e2e"]["gold"]["weighted_score"],
            "gold_claims_total": record["e2e"]["gold"]["claims_total"],
        }
        for system_key, prefix in (("osa", "osa_edu"), ("agent", "agent")):
            metrics = record["e2e"][system_key]
            row[f"{prefix}_implementation_rate"] = metrics["implementation_rate"]
            row[f"{prefix}_weighted_score"] = metrics["weighted_score"]
            row[f"{prefix}_absolute_error_pp"] = metrics["absolute_error_pp"]
            row[f"{prefix}_signed_error_pp"] = metrics["signed_error_pp"]
            row[f"{prefix}_weighted_absolute_error_pp"] = metrics[
                "weighted_absolute_error_pp"
            ]
            row[f"{prefix}_claim_count_deviation"] = metrics["claim_count_deviation"]
            row[f"{prefix}_claims_total"] = metrics["claims_total"]
        rows.append(row)
    return rows


def write_aggregate_csvs(
    root: Path,
    variant: str,
    tables: dict[str, list[dict[str, Any]]],
) -> None:
    write_csv(
        root / f"table1_claim_extraction_{variant}.csv",
        tables["extraction"],
        ["metric_key", "metric", "direction", "osa_edu", "agent"],
    )
    write_csv(
        root / f"table2_verification_{variant}.csv",
        tables["verification"],
        ["metric_key", "metric", "direction", "osa_edu"],
    )
    write_csv(
        root / f"table3_e2e_{variant}.csv",
        tables["e2e"],
        ["metric_key", "metric", "direction", "osa_edu", "agent"],
    )


def write_appendix_csvs(root: Path, records: list[dict[str, Any]]) -> None:
    extraction_rows = extraction_appendix_rows(records)
    extraction_fields = ["thesis_id"]
    for prefix in ("osa_edu", "agent"):
        extraction_fields.extend(
            f"{prefix}_{key}" for key, _, _, _ in EXTRACTION_METRICS
        )
        extraction_fields.extend([f"{prefix}_gold_total", f"{prefix}_extracted_total"])
    write_csv(
        root / "appendix_claim_extraction_by_thesis.csv",
        extraction_rows,
        extraction_fields,
    )

    verification_fields = ["thesis_id"]
    verification_fields.extend(key for key, _, _, _ in VERIFICATION_METRICS)
    verification_fields.extend(["included_total", "excluded_total"])
    write_csv(
        root / "appendix_verification_by_thesis.csv",
        verification_appendix_rows(records),
        verification_fields,
    )

    e2e_fields = [
        "thesis_id",
        "gold_implementation_rate",
        "gold_weighted_score",
        "gold_claims_total",
    ]
    for prefix in ("osa_edu", "agent"):
        e2e_fields.extend(
            [
                f"{prefix}_implementation_rate",
                f"{prefix}_weighted_score",
                f"{prefix}_absolute_error_pp",
                f"{prefix}_signed_error_pp",
                f"{prefix}_weighted_absolute_error_pp",
                f"{prefix}_claim_count_deviation",
                f"{prefix}_claims_total",
            ]
        )
    write_csv(
        root / "appendix_e2e_by_thesis.csv",
        e2e_appendix_rows(records),
        e2e_fields,
    )


def write_outputs(variant: str, include_appendix: bool, root: Path = ROOT) -> list[Path]:
    records = load_records(root)
    tables = aggregate_tables(records)

    write_aggregate_csvs(root, variant, tables)
    markdown = aggregate_markdown(tables, len(records))
    if include_appendix:
        markdown += "\n" + appendices_markdown(records)
        write_appendix_csvs(root, records)

    markdown_path = root / f"experiment_tables_{variant}.md"
    markdown_path.write_text(markdown, encoding="utf-8")

    outputs = [
        markdown_path,
        root / f"table1_claim_extraction_{variant}.csv",
        root / f"table2_verification_{variant}.csv",
        root / f"table3_e2e_{variant}.csv",
    ]
    if include_appendix:
        outputs.extend(
            [
                root / "appendix_claim_extraction_by_thesis.csv",
                root / "appendix_verification_by_thesis.csv",
                root / "appendix_e2e_by_thesis.csv",
            ]
        )
    return outputs
