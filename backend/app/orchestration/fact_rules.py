from __future__ import annotations

"""Fact-first normalization and deterministic verdicts for semantic rule pilots."""

from typing import Any
import regex as re

from ..rules.contracts import fact_items, is_fact_rule
from ..util import normalized_quote, unique

_STRONG_PROTOTYPE_CUE = re.compile(
    r"(?:\bпрототип\p{L}*\b|\bбазов\p{L}*\s+(?:подход\p{L}*|метод\p{L}*|решени\p{L}*|модел\p{L}*)|"
    r"\b(?:основой|базой)\s+.{0,80}\bслужит\b|\bопира\p{L}*\s+на\b|"
    r"\bрасшир\p{L}*\s+(?:классическ\p{L}*|базов\p{L}*)\s+(?:подход\p{L}*|метод\p{L}*|поиск\p{L}*)|"
    r"\bbaseline\b|\bbase\s+(?:approach|method|model)\b|\bbuilt\s+on\b|\bextends?\b)",
    re.I,
)

_COMPARISON_CUE = re.compile(
    r"(?:\bпо\s+сравнению\s+с\b|\bв\s+отличие\s+от\b|\bсравнен\p{L}*\s+с\b|"
    r"\bпревосход\p{L}*\b|\bустраня\p{L}*\s+ограничен\p{L}*\b|"
    r"\bснима\p{L}*\s+ограничен\p{L}*\b|\bзамен\p{L}*\b|"
    r"\bcompared\s+(?:with|to)\b|\bunlike\b|\boutperform\p{L}*\b|\blimitation\p{L}*\b)",
    re.I,
)

_LIMITATION_CUE = re.compile(
    r"(?:\bнедостат\p{L}*\b|\bограничен\p{L}*\b|\bне\s+(?:позволя\p{L}*|обеспечива\p{L}*|учитыва\p{L}*)\b|"
    r"\bзависим\p{L}*\b|\bнестабил\p{L}*\b|\bгаллюцинац\p{L}*\b|\bсмещени\p{L}*\b|"
    r"\blimitation\p{L}*\b|\bdrawback\p{L}*\b|\bfail\p{L}*\b)",
    re.I,
)

# A confident ``analogs=not_found`` is unsafe when the assigned chapter itself
# names an existing benchmark/method/system in a comparison, audit, baseline or
# prior-work context.  This guard never upgrades a fact to ``found``; it only
# prevents a red absence verdict and asks for manual/semantic disambiguation.
_ANALOG_CONTEXT_CUE = re.compile(
    r"(?:\bаналог\p{L}*\b|\bсуществующ\p{L}*\b|\bизвестн\p{L}*\b|\bраспростран\p{L}*\b|"
    r"\bсравнен\p{L}*\b|\bсопостав\p{L}*\b|\bальтернатив\p{L}*\b|\bбазов\p{L}*\b|"
    r"\bаудит\p{L}*\b|\bбенчмарк\p{L}*\b|\bbaseline\b|\bbenchmark\b|\bexisting\b|"
    r"\bprior\s+(?:work|method|approach)\b|\bcompared\s+(?:with|to)\b)",
    re.I,
)
_NAMED_TECH_ENTITY = re.compile(
    r"(?<![\p{L}\p{N}_])(?:[A-Z][A-Za-z0-9]*(?:[-–_/][A-Za-z0-9]+)*[A-Z0-9][A-Za-z0-9-_/]*|"
    r"[A-Z]{2,}[A-Za-z0-9@+.-]*)(?![\p{L}\p{N}_])"
)


def _has_grounded_analog_signal(text: str) -> bool:
    return bool(_ANALOG_CONTEXT_CUE.search(text) and _NAMED_TECH_ENTITY.search(text))


def _fragment_text(fragment: dict) -> str:
    return "\n".join(str(block.get("text") or "") for block in fragment.get("blocks") or [])


def _candidate_evidence(raw: Any, block_map: dict[str, dict]) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in raw[:12]:
        if not isinstance(item, dict):
            continue
        label = " ".join(str(item.get("label") or item.get("name") or "").split())[:160]
        relation = " ".join(str(item.get("relation") or item.get("reason") or "").split())[:240]
        evidence_rows = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        verified: list[dict] = []
        for row in evidence_rows[:4]:
            if not isinstance(row, dict):
                continue
            bid = str(row.get("blockId") or "").strip()
            quote = " ".join(str(row.get("quote") or "").split())
            block = block_map.get(bid)
            if not block or len(quote) < 4 or normalized_quote(quote) not in normalized_quote(str(block.get("text") or "")):
                continue
            key = (bid, normalized_quote(quote))
            if key in seen:
                continue
            seen.add(key)
            ev = {"blockId": bid, "quote": quote, "location": block.get("location", ""), "verified": True}
            if block.get("page") is not None:
                ev["page"] = block.get("page")
            verified.append(ev)
        if label or verified:
            out.append({"label": label, "relation": relation, "evidence": verified})
    return out


def enrich_matrix(rule_id: str, matrix: dict | None, fragment: dict) -> dict | None:
    """Apply precision-first invariants to an LLM fact matrix.

    The model may say ``not_found`` while simultaneously returning a plausible
    baseline candidate or while the actual fragment contains an explicit strong
    prototype/comparison cue. Python never upgrades such a cell to PASS; it merely
    prevents an unjustified red verdict by turning it into ``ambiguous``.
    """
    if not matrix or not is_fact_rule(rule_id):
        return matrix
    text = _fragment_text(fragment)
    adjusted = dict(matrix)
    rows = []
    for source in matrix.get("items") or []:
        item = dict(source)
        changes: list[str] = []
        candidates = list(item.get("candidates") or [])
        name = str(item.get("name") or "")
        status = str(item.get("status") or "ambiguous")

        if status == "not_found" and candidates:
            status = "ambiguous"
            changes.append("not_found→ambiguous: модель вернула возможные document-grounded candidates")

        analog_names = {"analogs", "analogs_inside_chapter"}
        if status == "not_found" and name in analog_names and _has_grounded_analog_signal(text):
            status = "ambiguous"
            changes.append("not_found→ambiguous: в назначенной области найден document-grounded сигнал существующего решения/бенчмарка")

        prototype_names = {"prototype", "prototype_inside_chapter"}
        disadvantage_names = {"prototype_disadvantages", "prototype_disadvantages_inside_chapter"}
        if status == "not_found" and name in prototype_names and _STRONG_PROTOTYPE_CUE.search(text):
            status = "ambiguous"
            changes.append("not_found→ambiguous: в назначенной области найден сильный маркер baseline/основы")
        if status == "not_found" and name in disadvantage_names and _LIMITATION_CUE.search(text):
            status = "ambiguous"
            changes.append("not_found→ambiguous: в назначенной области присутствует описание ограничений")
        if status == "not_found" and name == "comparison_with_prototype_in_chapter_conclusions" and _COMPARISON_CUE.search(text):
            status = "ambiguous"
            changes.append("not_found→ambiguous: в выводах присутствует явный сравнительный маркер")

        item["status"] = status
        if changes:
            item["pythonAdjustments"] = changes
        rows.append(item)
    adjusted["items"] = rows
    adjusted["factEngine"] = "3.6.0"
    return adjusted


def parse_candidates(raw: Any, block_map: dict[str, dict]) -> list[dict]:
    return _candidate_evidence(raw, block_map)


def _row_status(matrix: dict, name: str) -> str:
    row = next((item for item in matrix.get("items") or [] if item.get("name") == name), None)
    return str((row or {}).get("status") or "ambiguous")


def _fragment_decision(rule_id: str, matrix: dict | None) -> tuple[str, list[str]]:
    required = fact_items(rule_id)
    if not matrix or not matrix.get("complete"):
        return "uncertain", ["не подтверждено полное покрытие назначенной области"]
    statuses = {name: _row_status(matrix, name) for name in required}
    missing = [name for name, status in statuses.items() if status == "not_found"]
    ambiguous = [name for name, status in statuses.items() if status == "ambiguous"]

    if rule_id in {"CORE-2-3", "CORE-15"}:
        analog_name = required[0]
        prototype_name = required[1]
        disadvantage_name = required[2]
        if statuses.get(analog_name) == "not_found":
            return "violation", [analog_name]
        # A non-unique/uncertain prototype makes the dependent disadvantages
        # non-decidable. Precision takes priority over forcing a red verdict.
        if statuses.get(prototype_name) == "ambiguous":
            return "uncertain", [prototype_name, *([disadvantage_name] if statuses.get(disadvantage_name) != "found" else [])]
        if statuses.get(prototype_name) == "not_found":
            return "violation", [prototype_name]
        if statuses.get(disadvantage_name) == "ambiguous":
            return "uncertain", [disadvantage_name]
        if statuses.get(disadvantage_name) == "not_found":
            return "violation", [disadvantage_name]
        return "pass", []

    if rule_id == "CORE-8-2":
        name = required[0]
        if statuses.get(name) == "found":
            return "pass", []
        if statuses.get(name) == "not_found":
            return "violation", [name]
        return "uncertain", [name]

    if missing:
        return "violation", missing
    if ambiguous:
        return "uncertain", ambiguous
    return "pass", []


def aggregate_fact_rule(rule: dict, routed: dict, items: list[dict]) -> dict:
    """Calculate the final rule verdict from fact matrices, never LLM status."""
    rule_id = str(rule.get("id"))
    expected = list(routed.get("fragmentIds") or [])
    checked = [item for item in items if item.get("status") != "not_checked"]
    matrices = [row for item in checked for row in (item.get("coverageMatrix") or [])]
    by_fragment = {str(matrix.get("fragmentId")): matrix for matrix in matrices}
    decisions: list[tuple[str, str, list[str]]] = []
    for fragment_id in expected:
        matrix = by_fragment.get(str(fragment_id))
        status, details = _fragment_decision(rule_id, matrix)
        decisions.append((str(fragment_id), status, details))

    hard = [row for row in decisions if row[1] == "violation"]
    uncertain = [row for row in decisions if row[1] == "uncertain"]
    if hard:
        status = "violation"
    elif uncertain:
        status = "uncertain"
    elif decisions and all(row[1] == "pass" for row in decisions):
        status = "pass"
    else:
        status = "not_checked"

    labels = {str(matrix.get("fragmentId")): str(matrix.get("label") or matrix.get("fragmentId") or "фрагмент") for matrix in matrices}
    if status == "violation":
        parts = [f"{labels.get(fid, fid)}: не подтверждено {', '.join(details)}" for fid, _, details in hard]
        explanation = "Fact-first: после полного просмотра назначенной области отсутствуют обязательные факты. " + " ".join(parts)
    elif status == "uncertain":
        parts = [f"{labels.get(fid, fid)}: {', '.join(details)}" for fid, _, details in uncertain]
        explanation = "Fact-first: категорический вывод не формируется, потому что часть обязательных фактов неоднозначна. " + " ".join(parts)
    elif status == "pass":
        explanation = f"Fact-first: во всех {len(decisions)} назначенных фрагментах обязательные факты подтверждены полным просмотром."
    else:
        explanation = "Fact-first: обязательные фрагменты технически не удалось проверить."

    total = len(expected)
    complete_count = sum(1 for fid in expected if by_fragment.get(str(fid), {}).get("complete"))
    coverage = {
        "candidateCount": total,
        "checkedCandidateCount": len(checked),
        "packetCount": total,
        "checkedPacketCount": len(checked),
        "fraction": len(checked) / total if total else 0,
        "exhaustive": bool(routed.get("exhaustive") and total and complete_count == total),
    }
    evidence: list[dict] = []
    for matrix in matrices:
        for cell in matrix.get("items") or []:
            evidence.extend(cell.get("evidence") or [])
            for candidate in cell.get("candidates") or []:
                evidence.extend(candidate.get("evidence") or [])
    seen: set[tuple[str, str]] = set()
    deduped = []
    for ev in evidence:
        key = (str(ev.get("blockId")), normalized_quote(str(ev.get("quote") or "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)

    out = {
        "ruleId": rule_id,
        "status": status,
        "severity": rule.get("severity", "major"),
        "explanation": explanation,
        "confidence": 0,
        "evidence": deduped[:20] if status in {"violation", "uncertain"} else [],
        "evidenceStatus": "coverage_verified" if matrices and all(m.get("complete") for m in matrices) else "verified" if deduped else "not_required",
        "checkedBy": "fact-engine+llm-extractor",
        "coverage": coverage,
        "checkedFragments": unique(expected),
        "coverageMatrix": matrices,
        "factDecision": {
            "engine": "3.8",
            "llmVerdictIgnored": True,
            "fragments": [{"fragmentId": fid, "status": st, "details": details} for fid, st, details in decisions],
        },
    }
    if status == "violation":
        if rule_id in {"CORE-2-3", "CORE-15"}:
            out["fix"] = "В соответствующей главе явно связать уже рассматриваемые в работе существующие решения с ближайшей точкой сравнения и описать её ограничение, устраняемое предлагаемым результатом. Не добавлять внешние аналоги только ради выполнения правила."
        elif rule_id == "CORE-8-2":
            out["fix"] = "В выводах соответствующей главы добавить содержательное сопоставление разработанного результата с уже рассмотренным в работе известным решением или классом подходов и указать устраняемое ограничение."
    incomplete_expected = any(not bool(by_fragment.get(str(fid), {}).get("complete")) for fid in expected)
    if any(item.get("technicalIncomplete") for item in items) or incomplete_expected:
        out["technicalIncomplete"] = True
        if status == "not_checked":
            out["evidenceStatus"] = "not_required"
    return out
