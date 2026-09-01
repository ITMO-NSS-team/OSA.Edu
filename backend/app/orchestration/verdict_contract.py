from __future__ import annotations

from typing import Any

_ALLOWED = {"pass", "violation", "uncertain", "not_checked", "not_applicable"}


def technical_rule_result(rule: dict[str, Any], stage: str, exc: BaseException | str) -> dict[str, Any]:
    message = str(exc)
    return {
        "ruleId": str(rule.get("id") or ""),
        "status": "not_checked",
        "severity": str(rule.get("severity") or "major"),
        "explanation": f"Техническая ошибка на этапе {stage}: {message}",
        "confidence": 0,
        "evidence": [],
        "evidenceStatus": "not_required",
        "checkedBy": "system",
        "technicalIncomplete": True,
        "technicalStage": stage,
        "coverage": {"exhaustive": False},
    }


def _downgrade(result: dict[str, Any], explanation: str, *, technical: bool = False) -> dict[str, Any]:
    old = str(result.get("explanation") or "").strip()
    status = "not_checked" if technical else "uncertain"
    return {
        **result,
        "status": status,
        "confidence": 0,
        "explanation": (old + " " + explanation).strip(),
        "verdictContractAdjusted": True,
        **({"technicalIncomplete": True} if technical else {}),
    }


def enforce_verdict_contract(result: dict[str, Any]) -> dict[str, Any]:
    """Final safety gate shared by every rule engine.

    It does not invent a verdict. It only prevents a stronger verdict from
    surviving when the result itself says the scope/evidence was incomplete.
    """
    item = dict(result or {})
    status = str(item.get("status") or "")
    if status not in _ALLOWED:
        return _downgrade(item, "Получен неизвестный статус результата.", technical=True)

    if status in {"not_checked", "not_applicable"}:
        return item

    if bool(item.get("technicalIncomplete")):
        return _downgrade(item, "Полная техническая проверка правила не завершена.", technical=True)

    evidence_status = str(item.get("evidenceStatus") or "not_required")
    coverage = item.get("coverage") if isinstance(item.get("coverage"), dict) else {}
    exhaustive = coverage.get("exhaustive")

    # PASS is a universal claim over the assigned scope. It is forbidden when
    # the checker explicitly reports that this scope was not exhausted.
    if status == "pass" and exhaustive is False:
        return _downgrade(item, "PASS по неполной области запрещён: результат требует ручной проверки.")

    if status == "pass" and evidence_status == "rejected":
        return _downgrade(item, "Доказательная проверка отклонила основание окончательного PASS.")

    # Semantic/candidate violations must be grounded. Deterministic/structural
    # engines may prove a violation by exhaustive structural facts without a
    # quote, hence coverage_verified remains a valid evidence state.
    if status == "violation" and evidence_status == "rejected":
        return _downgrade(item, "Evidence-верификатор не подтвердил доказательство нарушения.")

    checked_by = str(item.get("checkedBy") or "")
    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
    llm_like = checked_by.startswith("llm") or "candidate" in checked_by
    if status == "violation" and llm_like and not evidence and evidence_status != "coverage_verified":
        return _downgrade(item, "LLM-нарушение без подтверждённого evidence не может быть окончательным.")

    return item


def enforce_verdict_contracts(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enforce_verdict_contract(item) for item in results]
