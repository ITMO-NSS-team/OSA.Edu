from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

BINARY_GOLD_LABELS = {
    "IMPLEMENTED": True,
    "NOT_IMPLEMENTED": False,
}
EXCLUDED_GOLD_LABELS = {
    "PARTIALLY_IMPLEMENTED",
    "NOT_STATICALLY_VERIFIABLE",
}
ALL_GOLD_LABELS = frozenset({*BINARY_GOLD_LABELS, *EXCLUDED_GOLD_LABELS})


def _rate(numerator: int, denominator: int) -> float:
    return round(float(numerator / denominator), 4) if denominator else 0.0


def _f1(true_positive: int, false_positive: int, false_negative: int) -> float:
    denominator = 2 * true_positive + false_positive + false_negative
    return round(float(2 * true_positive / denominator), 4) if denominator else 0.0


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Verification JSON must be an object: {path}")
    return payload


def _normalized_id(value: Any, *, role: str, index: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{role} claim #{index} must include a string claim_id")
    claim_id = " ".join(value.strip().split())
    if not claim_id:
        raise ValueError(f"{role} claim #{index} must include a non-empty claim_id")
    return claim_id


def _gold_claims(payload: dict[str, Any], *, path: Path) -> list[dict[str, Any]]:
    claim_verification = payload.get("claim_verification")
    if not isinstance(claim_verification, dict):
        raise ValueError(f"Gold verification JSON must contain claim_verification object: {path}")
    claims = claim_verification.get("claims")
    if not isinstance(claims, list):
        raise ValueError(f"Gold verification JSON must contain claim_verification.claims list: {path}")
    return claims


def _osa_claims(payload: dict[str, Any], *, path: Path) -> tuple[list[dict[str, Any]], str]:
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("claims"), list):
        return result["claims"], "stage_report"

    claim_verification = payload.get("claim_verification")
    if isinstance(claim_verification, dict) and isinstance(claim_verification.get("claims"), list):
        return claim_verification["claims"], "paper_analysis"

    raise ValueError(f"OSA verification JSON must contain result.claims list: {path}")


def _index_gold_claims(path: Path) -> tuple[dict[str, dict[str, Any]], Counter[str]]:
    payload = _read_json(path)
    claims = _gold_claims(payload, path=path)
    indexed: dict[str, dict[str, Any]] = {}
    statuses: Counter[str] = Counter()
    for index, item in enumerate(claims, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Gold claim #{index} must be an object")
        claim_id = _normalized_id(item.get("claim_id"), role="Gold", index=index)
        if claim_id in indexed:
            raise ValueError(f"Gold claim_id values must be unique; duplicate: {claim_id}")
        status_raw = item.get("status")
        if not isinstance(status_raw, str):
            raise ValueError(f"Gold claim {claim_id} must include a string status")
        status = status_raw.strip().upper()
        if status not in ALL_GOLD_LABELS:
            raise ValueError(f"Gold claim {claim_id} has unsupported status: {status_raw}")
        record = dict(item)
        record["claim_id"] = claim_id
        record["status"] = status
        indexed[claim_id] = record
        statuses[status] += 1
    return indexed, statuses


def _index_osa_claims(path: Path) -> tuple[dict[str, dict[str, Any]], str]:
    payload = _read_json(path)
    claims, source_format = _osa_claims(payload, path=path)
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(claims, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"OSA claim #{index} must be an object")
        claim_id = _normalized_id(item.get("claim_id"), role="OSA", index=index)
        if claim_id in indexed:
            raise ValueError(f"OSA claim_id values must be unique; duplicate: {claim_id}")
        implementation = item.get("implementation")
        if not isinstance(implementation, dict):
            raise ValueError(f"OSA claim {claim_id} must include implementation object")
        if type(implementation.get("implemented")) is not bool:
            raise ValueError(f"OSA claim {claim_id} must include boolean implementation.implemented")
        record = dict(item)
        record["claim_id"] = claim_id
        indexed[claim_id] = record
    return indexed, source_format


def _validate_claim_ids(gold_by_id: dict[str, Any], osa_by_id: dict[str, Any]) -> None:
    gold_ids = set(gold_by_id)
    osa_ids = set(osa_by_id)
    missing = sorted(gold_ids - osa_ids)
    extra = sorted(osa_ids - gold_ids)
    messages: list[str] = []
    if missing:
        messages.append(f"missing OSA claim_id values: {', '.join(missing[:10])}")
    if extra:
        messages.append(f"extra OSA claim_id values: {', '.join(extra[:10])}")
    if messages:
        raise ValueError("; ".join(messages))


def _implementation(record: dict[str, Any]) -> dict[str, Any]:
    implementation = record.get("implementation")
    return implementation if isinstance(implementation, dict) else {}


def _claim_text(record: dict[str, Any]) -> str:
    claim = record.get("claim")
    return claim if isinstance(claim, str) else ""


def _outcome(gold_implemented: bool, osa_implemented: bool) -> str:
    if gold_implemented and osa_implemented:
        return "TP"
    if not gold_implemented and osa_implemented:
        return "FP"
    if gold_implemented and not osa_implemented:
        return "FN"
    return "TN"


def _claim_result(
    claim_id: str,
    gold_record: dict[str, Any],
    osa_record: dict[str, Any],
    *,
    gold_implemented: bool,
    osa_implemented: bool,
    outcome: str,
) -> dict[str, Any]:
    implementation = _implementation(osa_record)
    return {
        "claim_id": claim_id,
        "claim": _claim_text(gold_record),
        "gold_status": gold_record["status"],
        "gold_implemented": gold_implemented,
        "osa_implemented": osa_implemented,
        "outcome": outcome,
        "osa_confidence": implementation.get("confidence"),
        "osa_evidence_file": implementation.get("evidence_file"),
        "gold_explanation": gold_record.get("explanation"),
        "osa_explanation": implementation.get("explanation"),
    }


def _excluded_claim(claim_id: str, gold_record: dict[str, Any], osa_record: dict[str, Any]) -> dict[str, Any]:
    implementation = _implementation(osa_record)
    return {
        "claim_id": claim_id,
        "claim": _claim_text(gold_record),
        "gold_status": gold_record["status"],
        "reason": "excluded_gold_status",
        "osa_implemented": implementation.get("implemented"),
        "osa_confidence": implementation.get("confidence"),
        "osa_evidence_file": implementation.get("evidence_file"),
        "gold_explanation": gold_record.get("explanation"),
        "osa_explanation": implementation.get("explanation"),
    }


def _metrics(confusion: Counter[str], *, excluded_total: int) -> dict[str, int | float]:
    tp = confusion["TP"]
    fp = confusion["FP"]
    fn = confusion["FN"]
    tn = confusion["TN"]
    included_total = tp + fp + fn + tn
    positive_f1 = _f1(tp, fp, fn)
    negative_f1 = _f1(tn, fn, fp)
    return {
        "included_total": included_total,
        "excluded_total": excluded_total,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "accuracy": _rate(tp + tn, included_total),
        "precision_implemented": _rate(tp, tp + fp),
        "recall_implemented": _rate(tp, tp + fn),
        "f1_implemented": positive_f1,
        "specificity": _rate(tn, tn + fp),
        "balanced_accuracy": round((_rate(tp, tp + fn) + _rate(tn, tn + fp)) / 2, 4),
        "macro_f1": round((positive_f1 + negative_f1) / 2, 4),
        "false_implementation_rate": _rate(fp, fp + tn),
    }


def evaluate_verification(gold_path: Path, osa_path: Path) -> dict[str, Any]:
    gold_by_id, gold_statuses = _index_gold_claims(gold_path)
    osa_by_id, osa_source_format = _index_osa_claims(osa_path)
    _validate_claim_ids(gold_by_id, osa_by_id)

    confusion: Counter[str] = Counter()
    claim_results: list[dict[str, Any]] = []
    excluded_claims: list[dict[str, Any]] = []
    for claim_id, gold_record in gold_by_id.items():
        osa_record = osa_by_id[claim_id]
        status = gold_record["status"]
        if status in EXCLUDED_GOLD_LABELS:
            excluded_claims.append(_excluded_claim(claim_id, gold_record, osa_record))
            continue

        gold_implemented = BINARY_GOLD_LABELS[status]
        osa_implemented = _implementation(osa_record)["implemented"]
        outcome = _outcome(gold_implemented, osa_implemented)
        confusion[outcome] += 1
        claim_results.append(
            _claim_result(
                claim_id,
                gold_record,
                osa_record,
                gold_implemented=gold_implemented,
                osa_implemented=osa_implemented,
                outcome=outcome,
            )
        )

    metrics = _metrics(confusion, excluded_total=len(excluded_claims))
    return {
        "schema_version": "1.0",
        "experiment": "claim_code_verification_1b",
        "meta": {
            "gold_verification_file": str(gold_path),
            "osa_verification_file": str(osa_path),
            "gold_claim_count": len(gold_by_id),
            "osa_claim_count": len(osa_by_id),
            "gold_status_counts": dict(sorted(gold_statuses.items())),
            "osa_source_format": osa_source_format,
        },
        "metrics": metrics,
        "confusion_matrix": {
            "true_positive": confusion["TP"],
            "false_positive": confusion["FP"],
            "false_negative": confusion["FN"],
            "true_negative": confusion["TN"],
        },
        "claim_results": claim_results,
        "excluded_claims": excluded_claims,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate Experiment 1B claims-code verification by claim_id.")
    parser.add_argument("--gold-verification", required=True, type=Path, help="Gold verification JSON.")
    parser.add_argument("--osa-verification", required=True, type=Path, help="OSA verification report JSON.")
    parser.add_argument("--output", type=Path, default=Path("verification_evaluation.json"))
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = evaluate_verification(args.gold_verification, args.osa_verification)
    except ValueError as exc:
        parser.error(str(exc))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    matrix = result["confusion_matrix"]
    metrics = result["metrics"]
    print(
        "Wrote "
        f"{args.output} "
        f"(TP={matrix['true_positive']}, FP={matrix['false_positive']}, "
        f"FN={matrix['false_negative']}, TN={matrix['true_negative']}, "
        f"included={metrics['included_total']}, excluded={metrics['excluded_total']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
