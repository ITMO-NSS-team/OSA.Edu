"""Structural VkrCheck criteria using OSA.Edu's confirmed Document Map."""

from __future__ import annotations

from .bibliography import bibliography_entries
from .common import evidence, result


def _uncertain(rule: dict, message: str) -> dict:
    return {**result(rule, "uncertain", message), "coverage": {"exhaustive": False}}


def _complete_map(document: dict) -> bool:
    review = (document.get("map") or {}).get("review") or {}
    return bool(review.get("confirmedByUser") and not document.get("extractionIncomplete"))


def _fact(document: dict, name: str) -> dict:
    return ((document.get("factStore") or {}).get("facts") or {}).get(name) or {}


def _evidence_for(fact: dict) -> list[dict]:
    blocks = fact.get("blocks") or []
    return [evidence(block, str(block.get("text") or "")[:180]) for block in blocks[:1]
            if block.get("id") and block.get("text")]


def run_vkrcheck_structural(rule: dict, document: dict) -> dict:
    rid = rule["id"]
    if rid == "VKR-SECTIONS":
        labels = {"introduction": "введение", "conclusion": "заключение", "bibliography": "список источников"}
        states = {name: _fact(document, name).get("status", "missing") for name in labels}
        unclear = [labels[name] for name, state in states.items() if state not in {"found", "missing"}]
        if unclear:
            return _uncertain(rule, "Неоднозначно выделены разделы: " + ", ".join(unclear) + ".")
        missing = [labels[name] for name, state in states.items() if state == "missing"]
        if missing and not _complete_map(document):
            return _uncertain(rule, "Не найдены разделы " + ", ".join(missing) + "; подтвердите карту документа вручную.")
        if missing:
            return result(rule, "violation", "Не найдены обязательные разделы: " + ", ".join(missing) + ".",
                          confidence=1, evidence_status="coverage_verified")
        return result(rule, "pass", "Введение, заключение и список источников найдены.",
                      [_evidence_for(_fact(document, name))[0] for name in labels
                       if _evidence_for(_fact(document, name))], confidence=1)

    if rid == "VKR-CHAPTERS":
        fact = _fact(document, "chapter")
        if fact.get("status") not in {"found", "missing"}:
            return _uncertain(rule, "Границы основных глав определены неоднозначно.")
        count = len([row for row in fact.get("candidates") or [] if row.get("status") == "found"])
        if count < 2 and not _complete_map(document):
            return _uncertain(rule, f"Найдено {count} глав; подтвердите карту документа вручную.")
        message = f"Найдено основных глав: {count}; ожидается от 2 до 8."
        return result(rule, "pass" if 2 <= count <= 8 else "violation", message,
                      [_evidence_for(row)[0] for row in (fact.get("candidates") or [])[:8]
                       if _evidence_for(row)], confidence=1,
                      evidence_status="coverage_verified")

    if rid == "VKR-INTRO-LENGTH":
        fact = _fact(document, "introduction")
        if fact.get("status") != "found":
            return _uncertain(rule, "Введение не выделено надёжно; объём не оценивается.")
        size = len("\n".join(str(block.get("text") or "") for block in fact.get("blocks") or []))
        return result(rule, "pass" if size >= 500 else "violation",
                      f"Объём введения: {size} символов; ориентир VkrCheck — не менее 500.",
                      _evidence_for(fact), confidence=1,
                      evidence_status="coverage_verified")

    if rid == "VKR-REFERENCES":
        fact = _fact(document, "bibliography")
        if fact.get("status") != "found":
            return _uncertain(rule, "Список источников не выделен надёжно; записи не подсчитаны.")
        entries, complete = bibliography_entries(fact.get("blocks") or [])
        if not complete:
            return _uncertain(rule, f"Распознано {len(entries)} записей, но разбор списка неполон.")
        return result(rule, "pass" if len(entries) >= 5 else "violation",
                      f"Распознано источников: {len(entries)}; ориентир VkrCheck — не менее 5.",
                      _evidence_for(fact), confidence=1,
                      evidence_status="coverage_verified")

    return _uncertain(rule, "Неизвестное структурное правило VkrCheck.")
