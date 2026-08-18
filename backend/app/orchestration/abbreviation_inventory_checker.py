from __future__ import annotations

"""LLM judgement over a Python-enumerated abbreviation inventory.

Experiment policy (3.9.3-rc1):
- Python discovers *all* abbreviation-like candidates and owns scope/evidence.
- The first LLM request receives the complete candidate inventory at once, with
  short first-use / heading contexts and the exact CORE abbreviation rules.
- The LLM decides whether every supplied candidate violates each rule.
- Python never invents new candidates or overrides semantic rule judgement; it
  only validates JSON completeness, attaches already-grounded evidence and
  aggregates candidate decisions into rule results.
- Missing rows get a targeted recovery request containing only the missing
  candidates. A partial response degrades to ``uncertain`` rather than a false
  pass. A total audit failure remains technically visible.
"""

import asyncio
import json
import os
from typing import Any

from ..checking.abbreviations import build_llm_abbreviation_inventory
from ..llm.client import ask_structured_json, is_fatal_provider_error
from ..util import empty_usage, merge_usage

ABBREVIATION_RULE_IDS = ("CORE-4-1", "CORE-4-2", "CORE-4-3", "CORE-12")
_ALLOWED_STATUS = {"pass", "violation", "uncertain", "not_applicable"}
_ALLOWED_ENTITY_TYPES = {
    "abbreviation",
    "method_or_algorithm",
    "model_name",
    "dataset_name",
    "named_resource",
    "metric_or_measure",
    "format_or_protocol",
    "identifier_or_code",
    "ordinary_text",
    "uncertain",
}


def _small_evidence(value: dict | None) -> dict | None:
    if not isinstance(value, dict) or not value:
        return None
    return {
        "blockId": str(value.get("blockId") or ""),
        "location": str(value.get("location") or ""),
        **({"page": value.get("page")} if value.get("page") is not None else {}),
        "quote": " ".join(str(value.get("quote") or "").split())[:900],
    }


def _prompt_inventory(inventory: list[dict]) -> list[dict]:
    rows=[]
    for item in inventory:
        rows.append({
            "id": item["candidateId"],
            "token": item["term"],
            "firstUse": _small_evidence(item.get("firstUse")),
            "headingUses": [x for x in (_small_evidence(ev) for ev in item.get("headingUses") or []) if x],
        })
    return rows


def build_abbreviation_llm_message(rules: list[dict], inventory: list[dict], *, recovery: bool = False) -> str:
    rule_rows=[]
    by_id={str(rule.get("id")): rule for rule in rules}
    for rid in ABBREVIATION_RULE_IDS:
        rule=by_id.get(rid)
        if not rule:
            continue
        rule_rows.append({
            "id": rid,
            "requirement": str(rule.get("requirement") or ""),
            "correctExample": str(rule.get("correctExample") or ""),
            "incorrectExample": str(rule.get("incorrectExample") or ""),
        })

    prefix = "ТОЧЕЧНЫЙ RECOVERY" if recovery else "ПОЛНЫЙ АУДИТ"
    return f'''{prefix} СОКРАЩЕНИЙ.

Python уже выполнил поиск по документу и передал тебе ПОЛНЫЙ список найденных abbreviation-like кандидатов для этой проверки. Не ищи новые обозначения и не добавляй токены, которых нет в CANDIDATES.

Твоя задача — для КАЖДОГО кандидата решить, нарушает ли он КАЖДОЕ из переданных правил. Используй только token и предоставленный локальный контекст. Не придумывай расшифровки или факты из внешних знаний.

Ключевые принципы:
- Сначала пойми по контексту, является ли token нормативно значимой аббревиатурой. Название модели, датасета, продукта, ресурса, идентификатор, обычный англоязычный фрагмент и т.п. не обязаны нарушать правила только из-за латиницы/верхнего регистра.
- CORE-4-1 оценивай по firstUse. Если при первом содержательном употреблении уже есть корректный полный русский термин перед аббревиатурой в скобках, нарушения нет. Если контекста недостаточно — uncertain.
- CORE-4-2 относится только к title/TOC/реальным заголовкам. Если headingUses пуст, для данного кандидата ставь not_applicable по CORE-4-2. Если headingUses есть, реши, является ли token именно запрещённой аббревиатурой в заголовке.
- CORE-4-3: для иностранной аббревиатуры нужен русский полный термин/перевод. Одна английская расшифровка не является русским переводом. Новую русскую аббревиатуру придумывать не требуется.
- CORE-12 оценивай как самостоятельное правило по firstUse и headingUses, а не просто копируй другое поле автоматически.
- pass = этот кандидат проверен и не нарушает правило; violation = есть нарушение; uncertain = данных недостаточно/тип неоднозначен; not_applicable = правило неприменимо к этому кандидату.
- Просмотри ВСЕ кандидаты. Не пропускай строки.

RULES:
{json.dumps(rule_rows, ensure_ascii=False, separators=(',', ':'))}

CANDIDATES:
{json.dumps(_prompt_inventory(inventory), ensure_ascii=False, separators=(',', ':'))}

Верни ТОЛЬКО JSON такой формы:
{{"decisions":[{{"id":"<candidate-id>","entityType":"abbreviation|method_or_algorithm|model_name|dataset_name|named_resource|metric_or_measure|format_or_protocol|identifier_or_code|ordinary_text|uncertain","r41":"pass|violation|uncertain|not_applicable","r42":"pass|violation|uncertain|not_applicable","r43":"pass|violation|uncertain|not_applicable","r12":"pass|violation|uncertain|not_applicable","reason":"очень кратко, почему"}}]}}

Для каждого id должна быть ровно одна строка.'''


def _parse_rows(value: Any, allowed_ids: set[str]) -> dict[str, dict]:
    rows=value.get("decisions", []) if isinstance(value, dict) else []
    out: dict[str, dict]={}
    if not isinstance(rows, list):
        return out
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        cid=str(raw.get("id") or "").strip()
        if cid not in allowed_ids:
            continue
        parsed={"id": cid}
        kind=str(raw.get("entityType") or "uncertain").strip().lower()
        parsed["entityType"] = kind if kind in _ALLOWED_ENTITY_TYPES else "uncertain"
        complete=True
        for key in ("r41", "r42", "r43", "r12"):
            status=str(raw.get(key) or "").strip().lower()
            if status not in _ALLOWED_STATUS:
                complete=False
                break
            parsed[key]=status
        if not complete:
            continue
        parsed["reason"]=" ".join(str(raw.get("reason") or "").split())[:500]
        out[cid]=parsed
    return out


def _evidence_for_rule(candidate: dict, rule_id: str) -> list[dict]:
    first=dict(candidate.get("firstUse") or {}) if candidate.get("firstUse") else None
    headings=[dict(ev) for ev in candidate.get("headingUses") or []]
    if rule_id == "CORE-4-2":
        evidence=headings[:3]
    elif rule_id == "CORE-12":
        evidence=[]
        if first:
            evidence.append(first)
        evidence.extend(headings[:2])
    else:
        evidence=[first] if first else headings[:1]
    out=[]
    for item in evidence:
        if not item:
            continue
        item["token"]=candidate.get("term")
        out.append(item)
    return out


def _coverage(total: int, terminal: int, ambiguous: int, responded: int) -> dict:
    return {
        "domain": "abbreviation_candidates",
        "candidateCount": total,
        "checkedCandidateCount": terminal,
        "respondedCandidateCount": responded,
        "terminalCandidateCount": terminal,
        "ambiguousCandidateCount": ambiguous,
        "exhaustive": total == terminal,
    }


def _aggregate_rule(rule: dict, inventory: list[dict], decisions: dict[str, dict]) -> dict:
    rid=str(rule.get("id") or "")
    key={"CORE-4-1":"r41", "CORE-4-2":"r42", "CORE-4-3":"r43", "CORE-12":"r12"}[rid]
    violations=[]
    ambiguous=[]
    term_findings=[]
    responded=0
    terminal=0

    for candidate in inventory:
        cid=str(candidate.get("candidateId") or "")
        row=decisions.get(cid)
        status=row.get(key) if row else "uncertain"
        if row:
            responded+=1
        if status in {"pass", "violation", "not_applicable"}:
            terminal+=1
        else:
            ambiguous.append(candidate)
        kind=(row or {}).get("entityType", "uncertain")
        term_findings.append({
            "term": candidate.get("term"),
            "kind": kind,
            "status": status,
            "requiresExpansion": rid in {"CORE-4-1", "CORE-12"},
            "requiresRussianExplanation": rid == "CORE-4-3",
            **({"firstUse": candidate.get("firstUse")} if candidate.get("firstUse") else {}),
        })
        if status == "violation":
            violations.append((candidate,row or {}))

    coverage=_coverage(len(inventory),terminal,len(inventory)-terminal,responded)
    evidence=[]
    finding_ids=[]
    for candidate,row in violations:
        evidence.extend(_evidence_for_rule(candidate,rid))
        finding_ids.append(f"abbr-llm:{rid}:{candidate.get('candidateId')}")
    # stable dedupe by block/token/quote
    unique=[]; seen=set()
    for ev in evidence:
        k=(str(ev.get("blockId") or ""),str(ev.get("token") or ""),str(ev.get("quote") or ""))
        if k in seen:
            continue
        seen.add(k); unique.append(ev)
    evidence=unique[:20]

    if violations:
        terms=list(dict.fromkeys(str(candidate.get("term")) for candidate,_ in violations))
        status="violation"
        if rid == "CORE-4-1":
            explanation="LLM-аудит полного Python-инвентаря подтвердил нарушение первого употребления для: " + ", ".join(terms) + "."
            fix="Для подтверждённых случаев оформить первое содержательное употребление согласно требованию правила."
        elif rid == "CORE-4-2":
            explanation="LLM-аудит полного Python-инвентаря подтвердил запрещённые аббревиатуры в названии/оглавлении/заголовках: " + ", ".join(terms) + "."
            fix="Для подтверждённых случаев убрать аббревиатуру из заголовка либо использовать полный термин согласно правилу."
        elif rid == "CORE-4-3":
            explanation="LLM-аудит полного Python-инвентаря подтвердил иностранные аббревиатуры без требуемого русского пояснения: " + ", ".join(terms) + "."
            fix="Добавить русский полный термин/перевод; исходную иностранную аббревиатуру можно сохранить."
        else:
            explanation="LLM-аудит полного Python-инвентаря подтвердил нарушения оформления сокращений: " + ", ".join(terms) + "."
            fix="Исправить подтверждённые случаи в соответствии с правилом."
    elif ambiguous:
        status="uncertain"
        terms=list(dict.fromkeys(str(candidate.get("term")) for candidate in ambiguous))
        explanation="LLM проверила инвентарь, но для части обозначений не удалось получить однозначный verdict: " + ", ".join(terms[:20]) + "."
        fix=None
    else:
        status="pass"
        explanation="LLM проверила весь Python-инвентарь обозначений; подтверждённых нарушений этого правила не найдено."
        fix=None

    result={
        "ruleId": rid,
        "status": status,
        "severity": rule.get("severity","major"),
        "explanation": explanation,
        "confidence": 1 if status in {"pass","violation"} else 0,
        "evidence": evidence,
        "evidenceStatus": "verified" if evidence else "not_required",
        "checkedBy": "llm-abbreviation-inventory",
        "coverage": coverage,
        "manualReviewCount": len(ambiguous),
        "termFindings": term_findings,
    }
    if fix:
        result["fix"]=fix
    if finding_ids:
        result["findingIds"]=finding_ids
    return result


def _technical_failure(rule: dict, message: str, candidate_count: int) -> dict:
    return {
        "ruleId": rule.get("id"),
        "status": "not_checked",
        "severity": rule.get("severity","major"),
        "explanation": message,
        "confidence": 0,
        "evidence": [],
        "evidenceStatus": "not_required",
        "checkedBy": "llm-abbreviation-inventory",
        "technicalIncomplete": True,
        "coverage": _coverage(candidate_count,0,candidate_count,0),
    }


async def execute_abbreviation_inventory_check(
    *,
    document: dict,
    rules: list[dict],
    provider: str,
    model: str,
    system_prompt: str,
) -> tuple[list[dict], dict, list[str]]:
    usage=empty_usage()
    usage["abbreviationMode"]="llm-inventory"
    warnings: list[str]=[]
    relevant=[rule for rule in rules if str(rule.get("id")) in ABBREVIATION_RULE_IDS]
    if not relevant:
        return [],usage,warnings

    inventory=build_llm_abbreviation_inventory(document)
    usage["abbreviationCandidateCount"]=len(inventory)
    if not inventory:
        results=[]
        for rule in relevant:
            result=_aggregate_rule(rule,[],{})
            results.append(result)
        return results,usage,warnings

    decisions: dict[str,dict]={}
    all_ids={str(item["candidateId"]) for item in inventory}
    primary_error: Exception | None=None
    try:
        response=await ask_structured_json(
            provider=provider,
            model=model,
            system_prompt=system_prompt,
            user_message=build_abbreviation_llm_message(relevant,inventory),
            operation="check",
            packets=1,
            candidates=len(inventory),
            max_completion_tokens=max(3500,min(12000,1800 + len(inventory)*85)),
        )
        merge_usage(usage,response.get("usage"))
        decisions.update(_parse_rows(response.get("value"),all_ids))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        merge_usage(usage,getattr(exc,"llm_usage",None))
        if is_fatal_provider_error(exc):
            raise
        primary_error=exc
        warnings.append(f"LLM-аудит полного списка сокращений не завершён: {exc}")

    # If the full-list request partially succeeded, recover only omitted rows.
    # If it failed completely, split the already-enumerated inventory into small
    # fallback packets so a transient provider error does not destroy the report.
    missing=[item for item in inventory if str(item["candidateId"]) not in decisions]
    recovery_calls=0
    if missing:
        chunk_size=max(1,int(os.getenv("ABBREVIATION_LLM_RECOVERY_CHUNK_SIZE","20") or 20))
        max_rounds=max(1,int(os.getenv("ABBREVIATION_LLM_RECOVERY_ROUNDS","1") or 1))
        for _round in range(max_rounds):
            if not missing:
                break
            next_missing=[]
            for offset in range(0,len(missing),chunk_size):
                chunk=missing[offset:offset+chunk_size]
                ids={str(item["candidateId"]) for item in chunk}
                try:
                    response=await ask_structured_json(
                        provider=provider,
                        model=model,
                        system_prompt=system_prompt,
                        user_message=build_abbreviation_llm_message(relevant,chunk,recovery=True),
                        operation="check",
                        packets=1,
                        candidates=len(chunk),
                        max_completion_tokens=max(2200,min(6000,1200 + len(chunk)*90)),
                    )
                    recovery_calls+=1
                    merge_usage(usage,response.get("usage"))
                    decisions.update(_parse_rows(response.get("value"),ids))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    recovery_calls+=1
                    merge_usage(usage,getattr(exc,"llm_usage",None))
                    if is_fatal_provider_error(exc):
                        raise
                    warnings.append(f"Recovery сокращений ({len(chunk)} кандидатов) не завершён: {exc}")
                next_missing.extend(item for item in chunk if str(item["candidateId"]) not in decisions)
            missing=next_missing

    usage["abbreviationRecoveryRequests"]=recovery_calls
    usage["abbreviationResolvedCandidates"]=len(decisions)
    usage["abbreviationUnresolvedCandidates"]=max(0,len(inventory)-len(decisions))

    # A total failure means no CORE-4 rule was actually checked. Partial failure
    # is represented by candidate-level uncertainty and does not falsely pass.
    if not decisions:
        detail=f"LLM не вернула ни одного решения по {len(inventory)} найденным обозначениям"
        if primary_error:
            detail += f": {primary_error}"
        return [_technical_failure(rule,detail,len(inventory)) for rule in relevant],usage,warnings

    if missing:
        warnings.append(f"LLM не вынесла решения для {len(missing)} из {len(inventory)} обозначений; они оставлены для ручной проверки.")

    return [_aggregate_rule(rule,inventory,decisions) for rule in relevant],usage,warnings
