from __future__ import annotations

import asyncio
import re
from typing import Any

from ..config import env_int
from ..document.fact_store import fact_store_prompt_text
from ..llm.client import ask_structured_json, is_fatal_provider_error
from ..util import empty_usage, merge_usage, normalized_quote, unique


_PASS_REQUIRES_EVIDENCE = {"CORE-2-1", "CORE-2-2", "CORE-14"}

_RULE_AGGREGATION_GUIDANCE = {
    "CORE-2-1": (
        "PASS только если в exhaustive-scan есть document-grounded сравнение результата автора "
        "с ближайшим/лучшим аналогом или прототипом и явно показано отличие. Наличие любого сравнения "
        "само по себе не доказывает, что сравнение выполнено с лучшим аналогом. Если сравнения есть, "
        "но статус 'лучшего/ближайшего' установить нельзя, выбери uncertain. VIOLATION допустим при "
        "полном покрытии, если адекватное сравнение не найдено."
    ),
    "CORE-2-2": (
        "Оцени всю работу, а не отдельный инженерный фрагмент. PASS допустим, если exhaustive-scan "
        "подтверждает собственно научные результаты (например, теоретические положения, методы, модели, "
        "доказательства или содержательные экспериментальные результаты), а работа не сводится только к "
        "реализации/внедрению. VIOLATION только если полное покрытие подтверждает обратное."
    ),
    "CORE-4-4": (
        "Для нарушения укажи конкретный специфический термин из evidence и объясни, почему его значение "
        "не раскрыто ни локально, ни в GLOBAL_DOCUMENT_FACTS. Не объявляй термин необъяснённым только из-за "
        "того, что определение находится в другом chunk. Полный PASS этим правилом может быть запрещён "
        "маршрутизацией как экспертно-субъективный — тогда итог должен остаться uncertain."
    ),
    "CORE-14": (
        "Проверяй точное требование правила по всему переданному scope. Ищи конкретные document-grounded "
        "сведения о внедрении/использовании и реквизитах: организации, сроки, акты/подтверждающие документы "
        "или адреса/идентификаторы открытого ПО. Не засчитывай общие обещания практической значимости как "
        "конкретное подтверждение внедрения."
    ),
    "CORE-3-7": (
        "Ищи только необоснованную авторскую похвалу: уникальность, исключительность, высокую эффективность "
        "и сходные восторженные оценки без локального измеримого подтверждения. Количественно подтверждённое "
        "сравнение или нейтральное описание не является нарушением."
    ),
    "CORE-11-6": (
        "Фиксируй только реально уменьшительно-ласкательные или разговорно-уменьшительные формы по смыслу "
        "контекста. Не классифицируй слово как нарушение по одному формальному суффиксу; технические термины, "
        "фамилии и нормальные словарные формы исключай."
    ),
}


_FULL_SCOPE_AGGREGATION_SYSTEM = """Ты выполняешь финальную агрегацию уже завершённого exhaustive-scan одного правила ВКР.
Тебе переданы только локальные сигналы и проверяемые цитаты из документа. Внешние знания запрещены.
Твоя задача — извлечь строго заданные факты из всей совокупности evidence, а не придумывать новое правило и не повторять локальные verdict.
Для status=found у положительного или отрицательного наблюдаемого факта обязательно укажи evidenceRefs.
status=not_found допустим только потому, что вызывающая система уже гарантирует exhaustive coverage всей обязательной области.
Если evidence допускает несколько интерпретаций, используй ambiguous.
Верни только JSON указанной пользователем схемы, без Markdown и без дополнительных ключей верхнего уровня."""


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("blockId") or ""), normalized_quote(str(item.get("quote") or "")))
        if not key[0] or not key[1] or key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _latest_by_fragment(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        fragment_id = str(item.get("fragmentId") or "")
        if fragment_id:
            result[fragment_id] = item
    return result


def _coverage(routed: dict, items: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    expected = [str(value) for value in routed.get("fragmentIds") or []]
    latest = _latest_by_fragment(items)
    checked = [latest[fid] for fid in expected if fid in latest and latest[fid].get("status") != "not_checked"]
    technical = any(
        fid not in latest or latest[fid].get("status") == "not_checked" or latest[fid].get("technicalIncomplete")
        for fid in expected
    )
    exhaustive = bool(routed.get("exhaustive") and expected and len(checked) == len(expected) and not technical)
    coverage = {
        "candidateCount": len(expected),
        "checkedCandidateCount": len(checked),
        "packetCount": len(expected),
        "checkedPacketCount": len(checked),
        "fraction": len(checked) / len(expected) if expected else 0,
        "exhaustive": exhaustive,
        "fullScope": True,
    }
    return coverage, checked


def _evidence_catalog(checked: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[tuple[str, str], str]]:
    evidence = _dedupe_evidence([ev for item in checked for ev in (item.get("evidence") or [])])
    refs: dict[tuple[str, str], str] = {}
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(evidence, start=1):
        ref = f"E{index}"
        key = (str(item.get("blockId") or ""), normalized_quote(str(item.get("quote") or "")))
        refs[key] = ref
        rows.append({"ref": ref, **item})
    return rows, refs


def _aggregation_message(
    *,
    rule: dict,
    routed: dict,
    checked: list[dict[str, Any]],
    fragment_by: dict[str, dict[str, Any]],
    fact_store: dict | None,
) -> tuple[str, dict[str, dict[str, Any]]]:
    catalog, refs = _evidence_catalog(checked)
    evidence_by_ref = {str(row["ref"]): {k: v for k, v in row.items() if k != "ref"} for row in catalog}
    latest = _latest_by_fragment(checked)
    chunk_rows: list[str] = []
    for fid in routed.get("fragmentIds") or []:
        fragment = fragment_by.get(str(fid)) or {}
        item = latest.get(str(fid)) or {}
        item_refs = []
        for ev in item.get("evidence") or []:
            key = (str(ev.get("blockId") or ""), normalized_quote(str(ev.get("quote") or "")))
            ref = refs.get(key)
            if ref:
                item_refs.append(ref)
        chunk_rows.append(
            f"CHUNK {fragment.get('scopeChunkIndex','?')}/{fragment.get('scopeChunkCount','?')} "
            f"id={fid}\n"
            f"LOCAL_STATUS (не финальный): {item.get('status','not_checked')}\n"
            f"LOCAL_EXPLANATION: {str(item.get('explanation') or '')[:1800]}\n"
            f"EVIDENCE_REFS: {', '.join(unique(item_refs)) or '—'}"
        )

    evidence_rows = []
    for row in catalog:
        page = f" page={row.get('page')}" if row.get("page") is not None else ""
        evidence_rows.append(
            f"{row['ref']} | block={row.get('blockId')}{page} | {row.get('quote','')}"
        )
    guidance = str(rule.get("ruleGuidance") or _RULE_AGGREGATION_GUIDANCE.get(str(rule.get("id"))) or "").strip()
    guidance_section = f"\nRULE-SPECIFIC AGGREGATION GUIDANCE:\n{guidance}\n" if guidance else ""
    global_facts = fact_store_prompt_text(
        fact_store,
        [*(rule.get("globalFactKeys") or []), *(rule.get("requiredFacts") or [])],
    )
    global_section = f"\nGLOBAL_DOCUMENT_FACTS:\n{global_facts}\n" if global_facts else ""
    allow_pass = routed.get("allowPass", True)
    message = f'''FINAL EXHAUSTIVE-SCOPE AGGREGATION.

Все {len(routed.get('fragmentIds') or [])} частей обязательной области правила были обработаны. Ни один chunk не является самостоятельным документом: локальные статусы ниже — только сигналы/evidence для финального вывода.

RULE {rule.get('id')}
Требование: {rule.get('requirement','')}
allowPass={str(bool(allow_pass)).lower()}
{guidance_section}{global_section}
CHUNK RESULTS:
{chr(10).join(chr(10) + row for row in chunk_rows)}

GROUNDED EVIDENCE CATALOG:
{chr(10).join(evidence_rows) if evidence_rows else '— evidence snippets не извлечены —'}

Правила финального вывода:
1. Используй только CHUNK RESULTS, GROUNDED EVIDENCE CATALOG и GLOBAL_DOCUMENT_FACTS. Внешние знания запрещены.
2. Coverage exhaustive: отсутствие можно использовать только потому, что проверены ВСЕ chunks; не переносить локальное отсутствие из одного chunk на весь документ.
3. Для pass у CORE-2-1, CORE-2-2 и CORE-14 выбери хотя бы один evidenceRef, реально поддерживающий выполнение правила.
4. Для violation с конкретным наблюдаемым нарушением выбери evidenceRefs. Если нарушение состоит именно в глобальном отсутствии обязательного элемента, evidenceRefs могут быть пустыми: доказательством тогда является exhaustive coverage.
5. uncertain используй при смысловой неоднозначности, а не при технической ошибке.
6. Не расширяй смысл RULE.

Верни только JSON:
{{"result":{{"status":"pass|violation|uncertain","explanation":"краткий финальный вывод по всей области","fix":"кратко, только если нужен","evidenceRefs":["E1"]}}}}
'''
    return message, evidence_by_ref


def _fact_aggregation_message(
    *,
    rule: dict,
    routed: dict,
    checked: list[dict[str, Any]],
    fragment_by: dict[str, dict[str, Any]],
    fact_store: dict | None,
) -> tuple[str, dict[str, dict[str, Any]], dict[str, Any]]:
    rule_id = str(rule.get("id") or "")
    contract = {"facts": [dict(value) for value in (rule.get("aggregationFacts") or [])]}
    catalog, refs = _evidence_catalog(checked)
    evidence_by_ref = {str(row["ref"]): {k: v for k, v in row.items() if k != "ref"} for row in catalog}
    latest = _latest_by_fragment(checked)
    chunk_rows: list[str] = []
    for fid in routed.get("fragmentIds") or []:
        fragment = fragment_by.get(str(fid)) or {}
        item = latest.get(str(fid)) or {}
        item_refs: list[str] = []
        for ev in item.get("evidence") or []:
            key = (str(ev.get("blockId") or ""), normalized_quote(str(ev.get("quote") or "")))
            ref = refs.get(key)
            if ref:
                item_refs.append(ref)
        chunk_rows.append(
            f"CHUNK {fragment.get('scopeChunkIndex','?')}/{fragment.get('scopeChunkCount','?')} id={fid}\n"
            f"LOCAL_STATUS (только сигнал): {item.get('status','not_checked')}\n"
            f"LOCAL_EXPLANATION: {str(item.get('explanation') or '')[:2200]}\n"
            f"EVIDENCE_REFS: {', '.join(unique(item_refs)) or '—'}"
        )

    evidence_rows: list[str] = []
    for row in catalog:
        page = f" page={row.get('page')}" if row.get("page") is not None else ""
        evidence_rows.append(f"{row['ref']} | block={row.get('blockId')}{page} | {row.get('quote','')}")

    facts_spec = "\n".join(
        f"- {spec['name']}: {spec['description']}"
        for spec in contract["facts"]
    )
    schema_items = ",".join(
        '{"name":"'+spec['name']+'","status":"found|not_found|ambiguous","reason":"кратко","evidenceRefs":["E1"]}'
        for spec in contract["facts"]
    )
    global_facts = fact_store_prompt_text(
        fact_store,
        [*(rule.get("globalFactKeys") or []), *(rule.get("requiredFacts") or [])],
    )
    global_section = f"\nGLOBAL_DOCUMENT_FACTS:\n{global_facts}\n" if global_facts else ""
    message = f'''FACT-FIRST FINAL AGGREGATION.

RULE {rule_id}
Требование: {rule.get('requirement','')}
Coverage уже подтверждён вызывающей системой: проверены ВСЕ {len(routed.get('fragmentIds') or [])} частей обязательной области.
{global_section}
ИЗВЛЕКИ РОВНО ЭТИ ФАКТЫ:
{facts_spec}

CHUNK RESULTS:
{chr(10).join(chr(10) + row for row in chunk_rows)}

GROUNDED EVIDENCE CATALOG:
{chr(10).join(evidence_rows) if evidence_rows else '— evidence snippets не извлечены —'}

Правила извлечения:
1. Локальный status не является финальным verdict и может быть uncertain даже при наличии полезного evidence.
2. Для status=found обязательно укажи один или несколько evidenceRefs, которые прямо подтверждают факт.
3. not_found означает: после просмотра всех chunks факт не подтверждён ни evidence, ни GLOBAL_DOCUMENT_FACTS.
4. ambiguous означает: есть правдоподобные сигналы, но они не позволяют однозначно установить факт.
5. Не создавай новые требования и не используй внешние знания.
6. Для CORE-4-4 конкретное первое употребление термина может быть evidenceRef, а отсутствие объяснения подтверждается exhaustive coverage всей области.

Верни только JSON:
{{"facts":[{schema_items}],"summary":"краткая сводка только по извлечённым фактам","fix":"краткая рекомендация, если из фактов следует нарушение; иначе пустая строка"}}
'''
    return message, evidence_by_ref, contract


def _full_scope_block_count(routed: dict, fragment_by: dict[str, dict[str, Any]]) -> int:
    ids: set[str] = set()
    for fid in routed.get("fragmentIds") or []:
        for block in (fragment_by.get(str(fid)) or {}).get("blocks") or []:
            block_id = str(block.get("id") or "")
            if block_id:
                ids.add(block_id)
    return len(ids)


def _parse_fact_aggregation_result(
    *,
    rule: dict,
    routed: dict,
    value: Any,
    evidence_by_ref: dict[str, dict[str, Any]],
    coverage: dict[str, Any],
    contract: dict[str, Any],
    total_blocks: int,
) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    raw_facts = payload.get("facts") if isinstance(payload.get("facts"), list) else []
    by_name = {str(row.get("name") or ""): row for row in raw_facts if isinstance(row, dict)}

    parsed_facts: list[dict[str, Any]] = []
    for spec in contract.get("facts") or []:
        name = str(spec["name"])
        row = by_name.get(name) or {}
        status = str(row.get("status") or "ambiguous").strip().lower()
        if status not in {"found", "not_found", "ambiguous"}:
            status = "ambiguous"
        refs = [str(ref) for ref in (row.get("evidenceRefs") or []) if str(ref) in evidence_by_ref]
        evidence = _dedupe_evidence([evidence_by_ref[ref] for ref in refs])
        reason = " ".join(str(row.get("reason") or "").split())[:700]
        # A positive observation without a source span is not a grounded fact.
        if status == "found" and not evidence:
            status = "ambiguous"
            reason = (reason + " found→ambiguous: отсутствует document-grounded evidenceRef.").strip()
        parsed_facts.append({
            "name": name,
            "label": spec.get("label") or name,
            "polarity": spec.get("polarity") or "required",
            "status": status,
            "reason": reason,
            "evidence": evidence,
        })

    failed: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    satisfied: list[dict[str, Any]] = []
    for fact in parsed_facts:
        polarity = fact["polarity"]
        status = fact["status"]
        if status == "ambiguous":
            ambiguous.append(fact)
        elif polarity == "required":
            (satisfied if status == "found" else failed).append(fact)
        elif polarity == "forbidden":
            (failed if status == "found" else satisfied).append(fact)
        else:
            ambiguous.append(fact)

    if failed:
        status = "violation"
    elif ambiguous:
        status = "uncertain"
    else:
        status = "pass"

    if status == "pass" and not routed.get("allowPass", True):
        status = "uncertain"
        explanation = (
            "Полный scope проверен; конкретного подтверждённого нарушения не найдено, "
            "но автоматический PASS для этого экспертного правила запрещён контрактом и требуется ручная оценка."
        )
    elif status == "violation":
        missing = [f["label"] for f in failed if f["polarity"] == "required" and f["status"] == "not_found"]
        forbidden = [f["label"] for f in failed if f["polarity"] == "forbidden" and f["status"] == "found"]
        parts = []
        if missing:
            parts.append("не подтверждены: " + "; ".join(missing))
        if forbidden:
            parts.append("обнаружено: " + "; ".join(forbidden))
        explanation = "Полный scope проверен; " + "; ".join(parts) + "."
    elif status == "pass":
        explanation = "Полный scope проверен; подтверждены обязательные факты: " + "; ".join(f["label"] for f in satisfied) + "."
    else:
        explanation = "Полный scope проверен, но неоднозначны факты: " + "; ".join(f["label"] for f in ambiguous) + "."

    # Preserve a short model summary only as auxiliary detail; deterministic fact
    # resolution above owns the verdict and cannot be overridden by prose.
    summary = " ".join(str(payload.get("summary") or "").split())[:900]
    if summary and not re.search(r'недостаточно данных для финального|невозможно сделать вывод', summary, re.I):
        explanation = f"{explanation} {summary}".strip()

    evidence = _dedupe_evidence([ev for fact in parsed_facts for ev in fact.get("evidence") or []])
    evidence_status = "verified" if evidence else "coverage_verified"
    matrix_items = [
        {
            "name": fact["name"],
            "status": fact["status"],
            "reason": fact["reason"],
            "evidence": fact.get("evidence") or [],
            "label": fact["label"],
        }
        for fact in parsed_facts
    ]
    result: dict[str, Any] = {
        "ruleId": str(rule.get("id") or ""),
        "status": status,
        "severity": rule.get("severity", "major"),
        "explanation": explanation,
        "confidence": 0,
        "evidence": evidence,
        "evidenceStatus": evidence_status,
        "checkedBy": "fact-aggregation+llm-extractor",
        "coverage": coverage,
        "checkedFragments": list(routed.get("fragmentIds") or []),
        "coverageMatrix": [{
            "fragmentId": "full-scope",
            "label": "Полная обязательная область",
            "complete": bool(coverage.get("exhaustive")),
            "checkedBlocks": total_blocks,
            "totalBlocks": total_blocks,
            "items": matrix_items,
        }],
        "fullScopeAggregation": {
            "chunkCount": len(routed.get("fragmentIds") or []),
            "exhaustive": bool(coverage.get("exhaustive")),
            "mode": "fact-first",
            "facts": [{
                "name": fact["name"], "status": fact["status"], "polarity": fact["polarity"],
                "evidenceCount": len(fact.get("evidence") or []),
            } for fact in parsed_facts],
        },
    }
    fix = " ".join(str(payload.get("fix") or "").split())[:900]
    if status == "violation" and fix:
        result["fix"] = fix
    return result


def _parse_final_result(
    *,
    rule: dict,
    routed: dict,
    value: Any,
    evidence_by_ref: dict[str, dict[str, Any]],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    payload = value.get("result") if isinstance(value, dict) and isinstance(value.get("result"), dict) else value
    payload = payload if isinstance(payload, dict) else {}
    status = str(payload.get("status") or "uncertain").strip().lower()
    if status not in {"pass", "violation", "uncertain"}:
        status = "uncertain"
    refs = [str(value) for value in payload.get("evidenceRefs") or [] if str(value) in evidence_by_ref]
    evidence = _dedupe_evidence([evidence_by_ref[ref] for ref in refs])
    rule_id = str(rule.get("id") or "")

    if status == "pass" and not routed.get("allowPass", True):
        status = "uncertain"
        explanation = (
            str(payload.get("explanation") or "")
            + " Полный scope проверен, но автоматический PASS для этого экспертного правила запрещён контрактом."
        ).strip()
    else:
        explanation = str(payload.get("explanation") or "Недостаточно данных для финального смыслового вывода.").strip()

    if status == "pass" and rule_id in _PASS_REQUIRES_EVIDENCE and not evidence:
        status = "uncertain"
        explanation = (
            explanation
            + " PASS по этому правилу требует document-grounded положительного evidence из полного scope."
        ).strip()

    evidence_status = "verified" if evidence else ("coverage_verified" if coverage.get("exhaustive") else "not_required")
    result: dict[str, Any] = {
        "ruleId": rule_id,
        "status": status,
        "severity": rule.get("severity", "major"),
        "explanation": explanation,
        "confidence": 0,
        "evidence": evidence,
        "evidenceStatus": evidence_status,
        "checkedBy": "llm",
        "coverage": coverage,
        "checkedFragments": list(routed.get("fragmentIds") or []),
        "fullScopeAggregation": {
            "chunkCount": len(routed.get("fragmentIds") or []),
            "exhaustive": bool(coverage.get("exhaustive")),
        },
    }
    fix = str(payload.get("fix") or "").strip()
    if fix:
        result["fix"] = fix
    return result


async def aggregate_full_scope_rule(
    *,
    rule: dict,
    routed: dict,
    items: list[dict[str, Any]],
    fragment_by: dict[str, dict[str, Any]],
    fact_store: dict | None,
    provider: str,
    model: str,
    system_prompt: str,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Produce one document-level verdict after every full-scope chunk was scanned."""
    usage = empty_usage()
    warnings: list[str] = []
    coverage, checked = _coverage(routed, items)
    rule_id = str(rule.get("id") or "")

    if not coverage.get("exhaustive"):
        technical = any(
            item.get("technicalIncomplete") or item.get("status") == "not_checked"
            for item in items
        ) or len(checked) < len(routed.get("fragmentIds") or [])
        result = {
            "ruleId": rule_id,
            "status": "uncertain",
            "severity": rule.get("severity", "major"),
            "explanation": (
                f"Полный scope не агрегирован: обработано {len(checked)} из "
                f"{len(routed.get('fragmentIds') or [])} частей обязательной области."
            ),
            "confidence": 0,
            "evidence": _dedupe_evidence([ev for item in checked for ev in (item.get("evidence") or [])]),
            "evidenceStatus": "verified" if any(item.get("evidence") for item in checked) else "not_required",
            "checkedBy": "llm",
            "coverage": coverage,
            "checkedFragments": [str(item.get("fragmentId")) for item in checked if item.get("fragmentId")],
            "fullScopeAggregation": {"chunkCount": len(routed.get("fragmentIds") or []), "exhaustive": False},
        }
        if technical:
            result["technicalIncomplete"] = True
        return result, usage, warnings

    aggregation_facts = [dict(value) for value in (rule.get("aggregationFacts") or [])]
    fact_contract = {"facts": aggregation_facts} if aggregation_facts else None
    if fact_contract:
        message, evidence_by_ref, fact_contract = _fact_aggregation_message(
            rule=rule, routed=routed, checked=checked, fragment_by=fragment_by, fact_store=fact_store,
        )
    else:
        message, evidence_by_ref = _aggregation_message(
            rule=rule, routed=routed, checked=checked, fragment_by=fragment_by, fact_store=fact_store,
        )

    attempts = max(1, env_int("FULL_SCOPE_AGGREGATION_ATTEMPTS", 2))
    last_error: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await ask_structured_json(
                provider=provider,
                model=model,
                # Do not reuse semantic-prompt.txt here: it requires {"results": [...]}
                # and used to conflict with the aggregation schema, producing a
                # valid JSON object that _parse_final_result interpreted as empty.
                system_prompt=_FULL_SCOPE_AGGREGATION_SYSTEM,
                user_message=message,
                operation="full_scope_aggregation",
                packets=1,
                candidates=1,
                max_completion_tokens=3200 if fact_contract else 2400,
            )
            merge_usage(usage, response.get("usage"))
            if fact_contract:
                result = _parse_fact_aggregation_result(
                    rule=rule, routed=routed, value=response.get("value"),
                    evidence_by_ref=evidence_by_ref, coverage=coverage, contract=fact_contract,
                    total_blocks=_full_scope_block_count(routed, fragment_by),
                )
            else:
                result = _parse_final_result(
                    rule=rule, routed=routed, value=response.get("value"),
                    evidence_by_ref=evidence_by_ref, coverage=coverage,
                )
            return result, usage, warnings
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            last_error = exc
            merge_usage(usage, getattr(exc, "llm_usage", None))
            if is_fatal_provider_error(exc):
                raise
            if attempt < attempts:
                usage['retries'] = int(usage.get('retries', 0)) + 1
                if getattr(exc, 'raw_response', None) is not None:
                    usage['structuredOutputResends'] = int(usage.get('structuredOutputResends', 0)) + 1
                await asyncio.sleep(0.4 * attempt)

    warnings.append(f"{rule_id}: финальная full-scope агрегация не завершена: {last_error}")
    return {
        "ruleId": rule_id,
        "status": "uncertain",
        "severity": rule.get("severity", "major"),
        "explanation": "Все chunks были проверены, но финальный structured-output агрегации не удалось получить.",
        "confidence": 0,
        "evidence": _dedupe_evidence([ev for item in checked for ev in (item.get("evidence") or [])]),
        "evidenceStatus": "verified" if any(item.get("evidence") for item in checked) else "coverage_verified",
        "checkedBy": "llm",
        "coverage": coverage,
        "checkedFragments": list(routed.get("fragmentIds") or []),
        "technicalIncomplete": True,
        "fullScopeAggregation": {"chunkCount": len(routed.get("fragmentIds") or []), "exhaustive": True},
    }, usage, warnings
