from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STATUSES = (
    "IMPLEMENTED",
    "PARTIALLY_IMPLEMENTED",
    "NOT_IMPLEMENTED",
    "NOT_STATICALLY_VERIFIABLE",
)
IMPLEMENTED = "IMPLEMENTED"
PARTIALLY_IMPLEMENTED = "PARTIALLY_IMPLEMENTED"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
NOT_STATICALLY_VERIFIABLE = "NOT_STATICALLY_VERIFIABLE"


@dataclass(frozen=True)
class LoadedRecords:
    records_by_id: dict[str, dict[str, Any]]
    source_format: str


@dataclass(frozen=True)
class PipelineEvaluation:
    summary: dict[str, Any]
    claims_format: str
    verification_format: str


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _claim_items(payload: Any, *, path: Path, role: str) -> tuple[list[Any], str]:
    if isinstance(payload, list):
        return payload, "bare"
    if not isinstance(payload, dict):
        raise ValueError(f"{role} claims JSON must be an object or a list: {path}")

    if "claims" in payload:
        items = payload["claims"]
        source_format = "claims"
    elif "result" in payload:
        result = payload["result"]
        if isinstance(result, list):
            items = result
            source_format = "result"
        elif isinstance(result, dict) and isinstance(result.get("claims"), list):
            items = result["claims"]
            source_format = "result.claims"
        else:
            raise ValueError(f"{role} claims JSON result must be a list or contain claims list: {path}")
    else:
        raise ValueError(f"{role} claims JSON must contain 'claims' or 'result': {path}")

    if not isinstance(items, list):
        raise ValueError(f"{role} claims field must be a list: {path}")
    return items, source_format


def _verification_items(payload: Any, *, path: Path, role: str) -> tuple[list[Any], str]:
    if not isinstance(payload, dict):
        raise ValueError(f"{role} verification JSON must be an object: {path}")

    claim_verification = payload.get("claim_verification")
    if isinstance(claim_verification, dict) and isinstance(claim_verification.get("claims"), list):
        return claim_verification["claims"], "claim_verification.claims"

    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("claims"), list):
        return result["claims"], "result.claims"

    raise ValueError(f"{role} verification JSON must contain claim_verification.claims or result.claims: {path}")


def _normalized_id(value: Any, *, role: str, index: int, source: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{role} {source} item #{index} must include a string claim_id")
    claim_id = " ".join(value.strip().split())
    if not claim_id:
        raise ValueError(f"{role} {source} item #{index} must include a non-empty claim_id")
    return claim_id


def _index_records(items: list[Any], *, role: str, source: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"{role} {source} item #{index} must be an object")
        claim_id = _normalized_id(item.get("claim_id"), role=role, index=index, source=source)
        if claim_id in indexed:
            raise ValueError(f"{role} {source} claim_id values must be unique; duplicate: {claim_id}")
        record = dict(item)
        record["claim_id"] = claim_id
        indexed[claim_id] = record
    return indexed


def load_claims(path: Path, *, role: str) -> LoadedRecords:
    items, source_format = _claim_items(_read_json(path), path=path, role=role)
    return LoadedRecords(
        records_by_id=_index_records(items, role=role, source="claims"),
        source_format=source_format,
    )


def load_verification(path: Path, *, role: str) -> LoadedRecords:
    items, source_format = _verification_items(_read_json(path), path=path, role=role)
    return LoadedRecords(
        records_by_id=_index_records(items, role=role, source="verification"),
        source_format=source_format,
    )


def _validate_pair_ids(
    claims: dict[str, dict[str, Any]],
    verification: dict[str, dict[str, Any]],
    *,
    role: str,
) -> None:
    claim_ids = set(claims)
    verification_ids = set(verification)
    missing = sorted(claim_ids - verification_ids)
    extra = sorted(verification_ids - claim_ids)

    messages: list[str] = []
    if missing:
        messages.append(f"missing verification for {len(missing)} {role} claim_id values: {', '.join(missing[:10])}")
    if extra:
        messages.append(
            f"extra verification for {len(extra)} nonexistent {role} claim_id values: {', '.join(extra[:10])}"
        )
    if messages:
        raise ValueError("; ".join(messages))


def _canonical_status(value: str) -> str:
    return value.strip().upper().replace("-", "_").replace(" ", "_")


def normalized_status(record: dict[str, Any], *, role: str, claim_id: str) -> str:
    status = record.get("status")
    if isinstance(status, str) and status.strip():
        normalized = _canonical_status(status)
        if normalized in STATUSES:
            return normalized
        raise ValueError(f"{role} verification claim {claim_id} has unsupported status: {status}")
    if "status" in record and status is not None:
        raise ValueError(f"{role} verification claim {claim_id} status must be a string")

    implementation = record.get("implementation")
    if isinstance(implementation, dict):
        implemented = implementation.get("implemented")
        if type(implemented) is bool:
            return IMPLEMENTED if implemented else NOT_IMPLEMENTED
        raise ValueError(f"{role} verification claim {claim_id} must include boolean implementation.implemented")

    raise ValueError(f"{role} verification claim {claim_id} must include status or implementation.implemented")


def _ratio_or_none(numerator: float, denominator: int) -> float | None:
    return round(float(numerator / denominator), 4) if denominator else None


def _percentage_points(value: float | None) -> float | None:
    return round(value * 100, 2) if value is not None else None


def _pipeline_summary(statuses: dict[str, str], *, claims_total: int) -> dict[str, Any]:
    counts = Counter(statuses.values())
    implemented = counts[IMPLEMENTED]
    partially_implemented = counts[PARTIALLY_IMPLEMENTED]
    not_implemented = counts[NOT_IMPLEMENTED]
    not_statically_verifiable = counts[NOT_STATICALLY_VERIFIABLE]
    evaluable = implemented + partially_implemented + not_implemented
    implementation_rate = _ratio_or_none(implemented, evaluable)
    weighted_score = _ratio_or_none(implemented + 0.5 * partially_implemented, evaluable)

    return {
        "claims_total": claims_total,
        "evaluable": evaluable,
        "implemented": implemented,
        "partially_implemented": partially_implemented,
        "not_implemented": not_implemented,
        "not_statically_verifiable": not_statically_verifiable,
        "implementation_rate": implementation_rate,
        "weighted_score": weighted_score,
    }


def evaluate_pipeline(*, role: str, claims_path: Path, verification_path: Path) -> PipelineEvaluation:
    claims = load_claims(claims_path, role=role)
    verification = load_verification(verification_path, role=role)
    _validate_pair_ids(claims.records_by_id, verification.records_by_id, role=role)
    statuses = {
        claim_id: normalized_status(record, role=role, claim_id=claim_id)
        for claim_id, record in verification.records_by_id.items()
    }
    return PipelineEvaluation(
        summary=_pipeline_summary(statuses, claims_total=len(claims.records_by_id)),
        claims_format=claims.source_format,
        verification_format=verification.source_format,
    )


def _absolute_error(system_value: float | None, gold_value: float | None) -> float | None:
    if system_value is None or gold_value is None:
        return None
    return round(abs(system_value - gold_value), 4)


def _claim_count_deviation(system_total: int, gold_total: int) -> float | None:
    return _ratio_or_none(abs(system_total - gold_total), gold_total)


def with_system_comparison(system: dict[str, Any], *, gold: dict[str, Any]) -> dict[str, Any]:
    implementation_error = _absolute_error(system["implementation_rate"], gold["implementation_rate"])
    weighted_error = _absolute_error(system["weighted_score"], gold["weighted_score"])
    return {
        **system,
        "absolute_error": implementation_error,
        "absolute_error_pp": _percentage_points(implementation_error),
        "weighted_absolute_error": weighted_error,
        "weighted_absolute_error_pp": _percentage_points(weighted_error),
        "claim_count_deviation": _claim_count_deviation(system["claims_total"], gold["claims_total"]),
    }


def _derive_thesis_id(output: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    if str(output.parent) not in {"", "."} and output.parent.name:
        return output.parent.name
    return Path.cwd().name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate end-to-end implementation-rate error for one thesis.")
    parser.add_argument("--gold-claims", required=True, type=Path, help="Gold claims JSON.")
    parser.add_argument("--gold-verification", required=True, type=Path, help="Gold verification JSON.")
    parser.add_argument("--osa-claims", required=True, type=Path, help="OSA claims JSON.")
    parser.add_argument("--osa-verification", required=True, type=Path, help="OSA verification JSON.")
    parser.add_argument("--agent-claims", required=True, type=Path, help="Agent claims JSON.")
    parser.add_argument("--agent-verification", required=True, type=Path, help="Agent verification JSON.")
    parser.add_argument("--output", type=Path, default=Path("e2e_results.json"))
    parser.add_argument("--thesis-id", default=None, help="Optional thesis identifier for the output JSON.")
    return parser


def _input_meta(
    *,
    claims_path: Path,
    verification_path: Path,
    evaluation: PipelineEvaluation,
) -> dict[str, str]:
    return {
        "claims_file": str(claims_path),
        "verification_file": str(verification_path),
        "claims_format": evaluation.claims_format,
        "verification_format": evaluation.verification_format,
    }


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        gold_eval = evaluate_pipeline(
            role="Gold",
            claims_path=args.gold_claims,
            verification_path=args.gold_verification,
        )
        osa_eval = evaluate_pipeline(
            role="OSA",
            claims_path=args.osa_claims,
            verification_path=args.osa_verification,
        )
        agent_eval = evaluate_pipeline(
            role="Agent",
            claims_path=args.agent_claims,
            verification_path=args.agent_verification,
        )
    except ValueError as exc:
        parser.error(str(exc))

    gold = gold_eval.summary
    osa = with_system_comparison(osa_eval.summary, gold=gold)
    agent = with_system_comparison(agent_eval.summary, gold=gold)
    thesis_id = _derive_thesis_id(args.output, args.thesis_id)
    result = {
        "schema_version": "1.0",
        "experiment": "paper_claims_e2e_implementation_rate",
        "thesis_id": thesis_id,
        "meta": {
            "status_values": list(STATUSES),
            "primary_metric": "absolute_implementation_rate_error_pp",
            "partial_weight": 0.5,
            "not_statically_verifiable": "excluded_from_denominator",
            "inputs": {
                "gold": _input_meta(
                    claims_path=args.gold_claims,
                    verification_path=args.gold_verification,
                    evaluation=gold_eval,
                ),
                "osa": _input_meta(
                    claims_path=args.osa_claims,
                    verification_path=args.osa_verification,
                    evaluation=osa_eval,
                ),
                "agent": _input_meta(
                    claims_path=args.agent_claims,
                    verification_path=args.agent_verification,
                    evaluation=agent_eval,
                ),
            },
        },
        "gold": gold,
        "osa": osa,
        "agent": agent,
        "comparison": {
            "primary_metric": "absolute_implementation_rate_error_pp",
            "gold_implementation_rate": gold["implementation_rate"],
            "gold_weighted_score": gold["weighted_score"],
            "osa_absolute_error_pp": osa["absolute_error_pp"],
            "agent_absolute_error_pp": agent["absolute_error_pp"],
            "osa_weighted_absolute_error_pp": osa["weighted_absolute_error_pp"],
            "agent_weighted_absolute_error_pp": agent["weighted_absolute_error_pp"],
            "osa_claim_count_deviation": osa["claim_count_deviation"],
            "agent_claim_count_deviation": agent["claim_count_deviation"],
        },
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Wrote {args.output} "
        f"(osa_error_pp={osa['absolute_error_pp']}, agent_error_pp={agent['absolute_error_pp']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
