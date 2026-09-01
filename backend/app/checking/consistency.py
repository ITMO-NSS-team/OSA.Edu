from __future__ import annotations

import re

from ..checking.common import evidence
from ..document.numbered_items import collect_unique_defense_items
from ..rules.manifest import manifest_entry


def _append_note(item: dict, note: str) -> dict:
    return {
        **item,
        "consistencyNotes": [*(item.get("consistencyNotes") or []), note],
    }


def _replace_status(item: dict, status: str, explanation: str, *, evidence_from: dict | None = None) -> dict:
    updated = {
        **item,
        "status": status,
        "explanation": explanation,
        "checkedBy": f"{item.get('checkedBy', 'system')}+consistency",
    }
    if evidence_from and evidence_from.get("evidence"):
        updated["evidence"] = list(evidence_from.get("evidence") or [])
        updated["evidenceStatus"] = evidence_from.get("evidenceStatus") or "verified"
    return updated




def _matrix_item_status(result: dict | None, name: str) -> list[str]:
    if not result:
        return []
    values=[]
    for row in result.get("coverageMatrix") or []:
        for item in row.get("items") or []:
            if item.get("name") == name:
                values.append(str(item.get("status") or ""))
    return values


def _prototype_status_by_statement(result: dict | None) -> dict[int, str]:
    out: dict[int, str] = {}
    if not result:
        return out
    for row in result.get("coverageMatrix") or []:
        label = str(row.get("label") or "")
        match = re.search(r"Положение\s+(\d+)", label, re.I)
        if not match:
            match = re.search(r"defense-chapter-(\d+)", str(row.get("fragmentId") or ""), re.I)
        if not match:
            continue
        number = int(match.group(1))
        prototype = next((item for item in row.get("items") or [] if item.get("name") == "prototype"), None)
        if prototype:
            out[number] = str(prototype.get("status") or "ambiguous")
    return out


def _matrix_fact_by_statement(result: dict | None, fact_name: str) -> dict[int, dict]:
    """Return fact cells keyed by the defence statement they are scoped to."""
    out: dict[int, dict] = {}
    if not result:
        return out
    for row in result.get("coverageMatrix") or []:
        label = str(row.get("label") or "")
        match = re.search(r"Положение\s+(\d+)", label, re.I)
        if not match:
            match = re.search(r"defense-chapter-(\d+)", str(row.get("fragmentId") or ""), re.I)
        if not match:
            continue
        cell = next((item for item in row.get("items") or [] if item.get("name") == fact_name), None)
        if cell:
            out[int(match.group(1))] = dict(cell)
    return out


def _conditional_evidence_requirement(rule_id: str) -> dict | None:
    entry = manifest_entry(rule_id)
    if not entry:
        return None
    value = entry.engine.model_dump(exclude_none=True).get("conditionalEvidence")
    if not isinstance(value, dict):
        return None
    condition_rule = str(value.get("conditionRule") or "")
    condition_fact = str(value.get("conditionFact") or "")
    eligible = {str(status) for status in value.get("eligibleStatuses") or []}
    if str(value.get("source") or "") != "defense_statements" or not condition_rule or not condition_fact or not eligible:
        return None
    return {
        "conditionRule": condition_rule,
        "conditionFact": condition_fact,
        "eligibleStatuses": eligible,
        "requireGroundedEvidence": bool(value.get("requireGroundedEvidence", False)),
        "distinctiveMarkers": [str(item) for item in value.get("distinctiveMarkers") or [] if str(item).strip()],
        "limitingRelationMarkers": [str(item) for item in value.get("limitingRelationMarkers") or [] if str(item).strip()],
    }


def _cell_has_grounded_evidence(cell: dict) -> bool:
    return bool(cell.get("evidence") or any(candidate.get("evidence") for candidate in cell.get("candidates") or []))


def _evidence_statement_number(item: dict) -> int | None:
    location = str(item.get("location") or "")
    match = re.search(r"положение\s+(\d+)", location, re.I)
    if match:
        return int(match.group(1))
    quote = str(item.get("quote") or "").strip()
    match = re.match(r"(?:Положение\s+)?(\d+)[.)]\s+", quote, re.I)
    return int(match.group(1)) if match else None


def _defense_statements_by_number(document: dict | None) -> dict[int, dict]:
    if not document:
        return {}
    items = collect_unique_defense_items((document.get("fields") or {}).get("defenseStatements") or [])
    return {int(item["number"]): item for item in items if item.get("number") is not None}


def _limiting_part_observed(statement: dict, contract: dict) -> bool:
    """Return whether the statement visibly contains a prototype-linked preamble."""
    text = " ".join(str(statement.get("text") or "").split())
    if not text:
        return False
    marker_match = None
    for marker in contract.get("distinctiveMarkers") or []:
        try:
            found = re.search(marker, text, re.I)
        except re.error:
            found = re.search(re.escape(marker), text, re.I)
        if found and (marker_match is None or found.start() < marker_match.start()):
            marker_match = found
    if marker_match is None:
        return False
    prefix = text[:marker_match.start()]
    if len(prefix.split()) < 3:
        return False
    return any(re.search(marker, prefix, re.I) for marker in contract.get("limitingRelationMarkers") or [])


def _apply_conditional_limiting_part(result: dict, *, document: dict | None, core23: dict | None, contract: dict) -> dict:
    """Restore a high-confidence omission finding after local evidence review."""
    if result.get("status") not in {"uncertain", "violation"}:
        return result
    statements = _defense_statements_by_number(document)
    facts = _matrix_fact_by_statement(core23, contract["conditionFact"])
    if not statements or not facts:
        return result
    missing = []
    for number, cell in facts.items():
        if str(cell.get("status") or "") not in contract["eligibleStatuses"]:
            continue
        if contract.get("requireGroundedEvidence") and not _cell_has_grounded_evidence(cell):
            continue
        statement = statements.get(number)
        if statement and not _limiting_part_observed(statement, contract):
            missing.append(statement)
    if not missing:
        return result
    ev = []
    for statement in missing:
        source = dict(statement.get("source") or statement.get("block") or {})
        quote = str(source.get("text") or statement.get("full") or statement.get("text") or "").strip()
        if source.get("id") and quote:
            ev.append(evidence(source, quote))
    updated = _replace_status(
        result,
        "violation",
        f"В {len(missing)} положении(ях) с подтверждённым прототипом не обнаружена ограничительная часть перед отличительной формулой.",
    )
    updated["evidence"] = ev
    updated["evidenceStatus"] = "coverage_verified"
    updated["confidence"] = 0.95
    updated["checkedBy"] = "conditional-limiting-part+consistency"
    updated["conditionalEvidence"] = {
        "conditionRule": contract["conditionRule"],
        "conditionFact": contract["conditionFact"],
        "missingStatementNumbers": [int(item["number"]) for item in missing],
    }
    return _append_note(updated, "CORE-1-3 восстановлен по statement-scoped условию и полной цитате положения.")


def apply_consistency_checks(results: list[dict], document: dict | None = None) -> list[dict]:
    # Work on copies so a post-processing invariant cannot mutate cached/local
    # detector results held elsewhere by the caller.
    prepared = [dict(item) for item in results]
    by = {item.get("ruleId"): item for item in prepared}

    core_11 = by.get("CORE-1-1")
    core_12 = by.get("CORE-1-2")
    core_13 = by.get("CORE-1-3")

    # CORE-1-1 explicitly contains the mandatory goal component checked by
    # CORE-1-2. Therefore CORE-1-1 cannot be PASS when CORE-1-2 has already
    # established that the component is absent.
    if core_11 and core_12 and core_11.get("status") in {"pass", "uncertain", "not_checked"}:
        if core_12.get("status") == "violation":
            replacement = _replace_status(
                core_11,
                "violation",
                "Структура положения не может считаться полностью корректной: связанное обязательное условие CORE-1-2 (цель после «с целью …») нарушено.",
                evidence_from=core_12,
            )
            replacement = _append_note(replacement, "Статус скорректирован по логическому инварианту CORE-1-1 ↔ CORE-1-2.")
            by["CORE-1-1"] = replacement
        elif core_12.get("status") in {"uncertain", "not_checked"}:
            replacement = _replace_status(
                core_11,
                "uncertain",
                "Полное соответствие структуре однозвенной формулы не подтверждено: обязательный компонент цели (CORE-1-2) остался неопределённым.",
            )
            replacement = _append_note(replacement, "PASS по CORE-1-1 понижен до UNCERTAIN, пока CORE-1-2 не подтверждён.")
            by["CORE-1-1"] = replacement

    core_11 = by.get("CORE-1-1")
    if core_11 and core_13 and core_11.get("status") == "pass" and core_13.get("status") == "violation":
        replacement = _replace_status(
            core_11,
            "violation",
            "Структура положения не может считаться полностью корректной: CORE-1-3 подтвердил отсутствие обязательной ограничительной части.",
            evidence_from=core_13,
        )
        replacement = _append_note(replacement, "Статус скорректирован по логическому инварианту CORE-1-1 ↔ CORE-1-3.")
        by["CORE-1-1"] = replacement

    # CORE-1-3 is conditional on whether a non-pioneering solution/prototype is
    # actually established. Reuse the exhaustive CORE-2-3 fact matrix instead of
    # allowing a free-form LLM statement such as «решение непионерское по
    # умолчанию» to create a red violation.
    core_13 = by.get("CORE-1-3")
    contract = _conditional_evidence_requirement("CORE-1-3")
    core_23 = by.get(contract["conditionRule"] if contract else "CORE-2-3")
    fact_name = contract["conditionFact"] if contract else "prototype"
    prototype_statuses = _matrix_item_status(core_23, fact_name)
    fact_by_statement = _matrix_fact_by_statement(core_23, fact_name)
    prototype_by_statement = {
        number: str(cell.get("status") or "ambiguous")
        for number, cell in fact_by_statement.items()
    }
    eligible_statuses = contract["eligibleStatuses"] if contract else {"found"}

    def is_supported(number: int) -> bool:
        cell = fact_by_statement.get(number, {})
        return (
            prototype_by_statement.get(number) in eligible_statuses
            and (not contract or not contract["requireGroundedEvidence"] or _cell_has_grounded_evidence(cell))
        )

    if core_13 and core_13.get("status") == "violation" and prototype_statuses:
        evidence_rows = list(core_13.get("evidence") or [])
        numbered = [(item, _evidence_statement_number(item)) for item in evidence_rows]
        known_numbers = [number for _item, number in numbered if number is not None]
        if known_numbers and prototype_by_statement:
            supported_numbers = {n for n in known_numbers if is_supported(n)}
            if not supported_numbers:
                replacement = _replace_status(
                    core_13,
                    "uncertain",
                    "CORE-1-3 условен для каждого положения отдельно. Evidence нарушения относится к положениям, для которых CORE-2-3 не подтвердил конкретный прототип; условие применимости красного verdict не доказано.",
                )
                replacement = _append_note(replacement, "Entity-level consistency 3.8: применимость CORE-1-3 проверена по номеру положения, а не глобально по документу.")
                by["CORE-1-3"] = replacement
            else:
                filtered = [item for item, number in numbered if number in supported_numbers or number is None]
                if len(filtered) < len(evidence_rows):
                    replacement = {**core_13, "evidence": filtered}
                    replacement = _append_note(replacement, "Entity-level consistency 3.8: evidence по положениям без подтверждённого прототипа исключено из CORE-1-3.")
                    by["CORE-1-3"] = replacement
        elif not any(is_supported(number) for number in fact_by_statement):
            replacement = _replace_status(
                core_13,
                "uncertain",
                "CORE-1-3 применяется только к непионерскому решению. В полной матрице CORE-2-3 конкретный прототип не подтверждён ни для одного положения, поэтому условие применимости нарушения не доказано.",
            )
            replacement = _append_note(replacement, "Нарушение CORE-1-3 понижено до UNCERTAIN: условие наличия прототипа не подтверждено матрицей фактов.")
            by["CORE-1-3"] = replacement
        elif any(status not in eligible_statuses for status in prototype_statuses):
            # Some other position has a prototype, but the evidence cannot be tied
            # to it. Global leakage was the main 3.7 false-positive mode.
            replacement = _replace_status(
                core_13,
                "uncertain",
                "CORE-1-3 не может использовать наличие прототипа у другого положения как доказательство применимости. Номер положения в evidence не удалось надёжно сопоставить с матрицей CORE-2-3.",
            )
            replacement = _append_note(replacement, "Entity-level consistency 3.8: глобальное наличие prototype больше не распространяется на все положения.")
            by["CORE-1-3"] = replacement

    # Evidence verification is local and cannot prove an omission by quoting a
    # short neighbourhood.  Once the prototype condition is grounded for the
    # same statement, the complete mapped statement is auditable evidence of a
    # missing limiting part.
    core_13 = by.get("CORE-1-3")
    if core_13 and contract and core_23:
        by["CORE-1-3"] = _apply_conditional_limiting_part(
            core_13, document=document, core23=core_23, contract=contract
        )

    # Reconcile CORE-1-1 after the entity-level CORE-1-3 decision. In 3.7 the
    # earlier invariant could make CORE-1-1 red and then CORE-1-3 was downgraded,
    # leaving a stale contradiction.
    core_11 = by.get("CORE-1-1")
    core_12 = by.get("CORE-1-2")
    core_13 = by.get("CORE-1-3")
    core13_driven = any("CORE-1-1 ↔ CORE-1-3" in str(note) for note in (core_11 or {}).get("consistencyNotes", []))
    if core_11 and core_11.get("status") == "violation" and core13_driven and core_13 and core_13.get("status") == "uncertain":
        if not core_12 or core_12.get("status") != "violation":
            replacement = _replace_status(
                core_11,
                "uncertain",
                "Полная структура положения остаётся неопределённой: условный компонент CORE-1-3 не подтверждён для соответствующего положения.",
            )
            replacement = _append_note(replacement, "CORE-1-1 синхронизирован с entity-level результатом CORE-1-3.")
            by["CORE-1-1"] = replacement

    # CORE-1-3 is conditional on whether the solution is pioneering. A document-only
    # PASS without evidence cannot establish that external novelty condition. It is
    # safer to keep the rule uncertain than to silently approve the missing
    # limiting part when evidence does not actually establish a prototype relation.
    core_13 = by.get("CORE-1-3")
    if core_13 and core_13.get("status") == "pass" and not core_13.get("evidence"):
        replacement = _replace_status(
            core_13,
            "uncertain",
            "Условие CORE-1-3 зависит от пионерского характера решения. В переданном тексте нет проверяемого доказательства, позволяющего автоматически подтвердить это условие или наличие требуемой ограничительной части.",
        )
        replacement = _append_note(replacement, "Без доказательства пионерского характера PASS по условному правилу не выставляется.")
        by["CORE-1-3"] = replacement

    notes: dict[str, list[str]] = {}
    pairs = [("CORE-8-1", "CORE-8-2"), ("CORE-9-1", "CORE-9-4"), ("CORE-6-3", "CORE-6-4")]
    for left, right in pairs:
        a, b = by.get(left), by.get(right)
        if not a or not b:
            continue
        if a.get("status") == "not_applicable" and b.get("status") in {"pass", "violation"}:
            notes.setdefault(right, []).append(f"{left} не применимо, поэтому результат {right} следует интерпретировать отдельно.")
        if a.get("status") == "uncertain" and b.get("status") == "pass":
            notes.setdefault(right, []).append(f"Связанное правило {left} осталось неопределённым.")

    out: list[dict] = []
    for original in prepared:
        item = by.get(original.get("ruleId"), original)
        if item.get("ruleId") in notes:
            item = {
                **item,
                "consistencyNotes": [*(item.get("consistencyNotes") or []), *notes[item["ruleId"]]],
            }
        out.append(item)
    return out
