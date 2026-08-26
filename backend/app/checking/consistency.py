from __future__ import annotations

import re


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


def _evidence_statement_number(item: dict) -> int | None:
    location = str(item.get("location") or "")
    match = re.search(r"положение\s+(\d+)", location, re.I)
    if match:
        return int(match.group(1))
    quote = str(item.get("quote") or "").strip()
    match = re.match(r"(?:Положение\s+)?(\d+)[.)]\s+", quote, re.I)
    return int(match.group(1)) if match else None


def apply_consistency_checks(results: list[dict]) -> list[dict]:
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
    if core_11 and core_12 and core_11.get("status") == "pass":
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
    core_23 = by.get("CORE-2-3")
    prototype_statuses = _matrix_item_status(core_23, "prototype")
    prototype_by_statement = _prototype_status_by_statement(core_23)
    if core_13 and core_13.get("status") == "violation" and prototype_statuses:
        evidence_rows = list(core_13.get("evidence") or [])
        numbered = [(item, _evidence_statement_number(item)) for item in evidence_rows]
        known_numbers = [number for _item, number in numbered if number is not None]
        if known_numbers and prototype_by_statement:
            supported_numbers = {n for n in known_numbers if prototype_by_statement.get(n) == "found"}
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
        elif "found" not in prototype_statuses:
            replacement = _replace_status(
                core_13,
                "uncertain",
                "CORE-1-3 применяется только к непионерскому решению. В полной матрице CORE-2-3 конкретный прототип не подтверждён ни для одного положения, поэтому условие применимости нарушения не доказано.",
            )
            replacement = _append_note(replacement, "Нарушение CORE-1-3 понижено до UNCERTAIN: условие наличия прототипа не подтверждено матрицей фактов.")
            by["CORE-1-3"] = replacement
        elif any(status != "found" for status in prototype_statuses):
            # Some other position has a prototype, but the evidence cannot be tied
            # to it. Global leakage was the main 3.7 false-positive mode.
            replacement = _replace_status(
                core_13,
                "uncertain",
                "CORE-1-3 не может использовать наличие прототипа у другого положения как доказательство применимости. Номер положения в evidence не удалось надёжно сопоставить с матрицей CORE-2-3.",
            )
            replacement = _append_note(replacement, "Entity-level consistency 3.8: глобальное наличие prototype больше не распространяется на все положения.")
            by["CORE-1-3"] = replacement

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
