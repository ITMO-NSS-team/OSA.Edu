from __future__ import annotations


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

    # CORE-1-3 is conditional on whether the solution is pioneering. A document-only
    # PASS without evidence cannot establish that external novelty condition. It is
    # safer to keep the rule uncertain than to silently approve the missing
    # limiting part, which happened in the Bashkova regression case.
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
