from __future__ import annotations

import asyncio
import os
from typing import Any

from ..checking.common import evidence
from ..document.numbered_items import collect_unique_defense_items
from ..llm.client import ask_structured_json, is_fatal_provider_error
from ..rules.manifest import manifest_entry
from ..util import empty_usage, merge_usage

_ALLOWED = {"confirmed", "rejected", "uncertain"}


def _normalise_marker(value: str) -> str:
    return " ".join(str(value or "").replace("\u00ad", "").lower().split())


def _literal_requirement(rule_id: str) -> dict[str, Any] | None:
    entry = manifest_entry(rule_id)
    if not entry:
        return None
    value = entry.engine.model_dump(exclude_none=True).get("literalRequirement")
    if not isinstance(value, dict):
        return None
    markers = [_normalise_marker(item) for item in value.get("markers") or [] if _normalise_marker(item)]
    if value.get("source") != "defense_statements" or value.get("quantifier") != "each_item" or not markers:
        return None
    return {"markers": markers}


def _conditional_requirement(rule_id: str) -> dict[str, Any] | None:
    """Read a declarative cross-rule precondition for a semantic finding."""
    entry = manifest_entry(rule_id)
    if not entry:
        return None
    value = entry.engine.model_dump(exclude_none=True).get("conditionalEvidence")
    if not isinstance(value, dict):
        return None
    condition_rule = str(value.get("conditionRule") or "").strip()
    condition_fact = str(value.get("conditionFact") or "").strip()
    if str(value.get("source") or "").strip() != "defense_statements" or not condition_rule or not condition_fact:
        return None
    return {
        "conditionRule": condition_rule,
        "conditionFact": condition_fact,
        "observableRequirement": str(value.get("observableRequirement") or "").strip(),
    }


def _apply_literal_requirements(document: dict, rules: list[dict], results: list[dict]) -> list[dict]:
    """Apply manifest-declared literal obligations after semantic verification.

    A semantic critic may accept paraphrases for semantic rules. It must not erase
    a violation where the norm itself requires a literal construction in every
    complete, mapped defence statement.
    """
    by_rule = {str(rule.get("id") or ""): rule for rule in rules}
    statements = collect_unique_defense_items((document.get("fields") or {}).get("defenseStatements") or [])
    if not statements:
        return results
    prepared = [dict(result) for result in results]
    for index, result in enumerate(prepared):
        rule_id = str(result.get("ruleId") or "")
        contract = _literal_requirement(rule_id)
        if not contract or rule_id not in by_rule:
            continue
        markers = contract["markers"]
        missing = [
            item for item in statements
            if any(marker not in _normalise_marker(str(item.get("text") or "")) for marker in markers)
        ]
        if not missing:
            continue
        items = []
        for item in missing:
            source = dict(item.get("source") or item.get("block") or {})
            if not source.get("id"):
                continue
            quote = str(source.get("text") or item.get("full") or item.get("text") or "")
            if quote:
                items.append(evidence(source, quote))
        marker_text = ", ".join(f"«{marker}»" for marker in markers)
        prepared[index] = {
            **result,
            "status": "violation",
            "explanation": (
                f"В {len(missing)} из {len(statements)} положений отсутствует обязательная "
                f"буквальная конструкция {marker_text}."
            ),
            "evidence": items,
            "evidenceStatus": "coverage_verified",
            "checkedBy": "literal-requirement+scope",
            "confidence": 1.0,
            "coverage": {
                "candidateCount": len(statements),
                "checkedCandidateCount": len(statements),
                "fraction": 1.0,
                "exhaustive": True,
            },
            "literalRequirement": {"markers": markers, "missingStatementNumbers": [item.get("number") for item in missing]},
        }
    return prepared


def _local_context(document: dict, block_id: str) -> str:
    blocks = document.get("blocks", [])
    index = {str(block.get("id")): i for i, block in enumerate(blocks)}
    pos = index.get(str(block_id))
    if pos is None:
        return ""
    window = blocks[max(0, pos - 1):min(len(blocks), pos + 2)]
    return "\n".join(f"BLOCK {b.get('id')} | type={b.get('type')}\n{b.get('text','')}" for b in window)


def _message(document: dict, rules: dict[str, dict], findings: list[dict]) -> str:
    chunks=[]
    for item in findings:
        rule=rules.get(str(item.get("ruleId"))) or {}
        condition = _conditional_requirement(str(item.get("ruleId") or ""))
        evidence=item.get("evidence") or {}
        chunks.append(
            f"FINDING {item.get('findingId')}\n"
            f"RULE {item.get('ruleId')}: {rule.get('requirement','')}\n"
            f"FIRST_PASS_EXPLANATION: {item.get('explanation','')}\n"
            f"QUOTE: {evidence.get('quote','')}\n"
            f"LOCAL_CONTEXT:\n{_local_context(document, str(evidence.get('blockId') or ''))}"
            + (
                f"\nCONDITIONAL_REQUIREMENT: внешнее условие проверяется отдельно правилом "
                f"{condition['conditionRule']} по факту {condition['conditionFact']}. "
                f"Оцени только наблюдаемую часть: {condition['observableRequirement'] or rule.get('requirement','')}"
                if condition else ""
            )
        )
    return '''Ты независимый evidence verifier для отчёта нормоконтроля. Первый LLM уже вынес semantic violation и указал точную цитату из документа.

Не ищи новые нарушения. Для каждого FINDING оцени только одно: доказывает ли приведённая цитата вместе с соседним локальным контекстом нарушение именно данного RULE.

Вердикты:
- confirmed - evidence действительно поддерживает нарушение правила;
- rejected - evidence существует в документе, но не доказывает заявленное нарушение или прямо ему противоречит;
- uncertain - локального контекста недостаточно, чтобы надёжно подтвердить нарушение.

Будь консервативен: отсутствие слова само по себе не нарушение, если правило смысловое и эквивалентный смысл присутствует. Не подтверждай вывод только потому, что FIRST_PASS_EXPLANATION звучит уверенно. Для FINDING с CONDITIONAL_REQUIREMENT не отвергай доказательство лишь потому, что внешнее условие не видно в LOCAL_CONTEXT: его проверит Python по указанной fact-матрице. Но всё равно отвергни finding, если цитата не подтверждает наблюдаемую часть правила.

''' + "\n\n".join(chunks) + '''

Верни только JSON:
{"decisions":[{"findingId":"semantic:CORE-X:0","verdict":"confirmed|rejected|uncertain","reason":"коротко"}]}
'''


async def verify_semantic_evidence(
    *,
    document: dict,
    rules: list[dict],
    results: list[dict],
    provider: str,
    model: str,
    system_prompt: str,
) -> tuple[list[dict], dict, list[str]]:
    """Second-pass support verification for evidence-backed semantic violations.

    This never resends the thesis. It sends only the rule, exact quote and a
    one-block neighbourhood. Absence/coverage-based violations, deterministic
    checks and candidate-first results are intentionally excluded.
    """
    usage=empty_usage()
    warnings: list[str]=[]
    if os.getenv("SEMANTIC_EVIDENCE_CRITIC_ENABLED", "1").strip().lower() in {"0","false","no","off"}:
        return results, usage, warnings

    findings=[]
    for result_index, result in enumerate(results):
        if result.get("status") != "violation":
            continue
        checked_by=str(result.get("checkedBy") or "")
        if checked_by != "llm":
            continue
        if result.get("evidenceStatus") != "verified":
            continue
        # Coverage-matrix rules prove both presence and absence over an exhaustive
        # assigned area. A three-block local critic cannot re-prove a global
        # absence and must not override that stronger contract.
        if result.get("coverageMatrix"):
            continue
        for evidence_index, evidence in enumerate(result.get("evidence") or []):
            if not evidence.get("blockId") or not evidence.get("quote"):
                continue
            findings.append({
                "findingId": f"semantic:{result.get('ruleId')}:{result_index}:{evidence_index}",
                "ruleId": result.get("ruleId"),
                "resultIndex": result_index,
                "evidenceIndex": evidence_index,
                "explanation": result.get("explanation", ""),
                "evidence": evidence,
            })
    if not findings:
        return _apply_literal_requirements(document, rules, results), usage, warnings

    batch_size=max(1,int(os.getenv("SEMANTIC_EVIDENCE_CRITIC_BATCH_SIZE","10") or 10))
    rule_map={str(rule.get("id")):rule for rule in rules}
    decisions: dict[str,str]={}
    failed_ids: set[str]=set()
    critic_attempts=max(1,int(os.getenv("SEMANTIC_EVIDENCE_CRITIC_ATTEMPTS","2") or 2))
    for offset in range(0,len(findings),batch_size):
        pending=list(findings[offset:offset+batch_size])
        last_error=None
        for attempt in range(1,critic_attempts+1):
            if not pending:
                break
            try:
                response=await ask_structured_json(
                    provider=provider,
                    model=model,
                    system_prompt=system_prompt,
                    user_message=_message(document,rule_map,pending),
                    operation="check",
                    packets=1,
                    candidates=len(pending),
                    max_completion_tokens=3500,
                )
                merge_usage(usage,response.get("usage"))
                value=response.get("value") if isinstance(response,dict) else None
                rows=value.get("decisions",[]) if isinstance(value,dict) else []
                for row in rows if isinstance(rows,list) else []:
                    if not isinstance(row,dict): continue
                    fid=str(row.get("findingId") or "")
                    verdict=str(row.get("verdict") or "uncertain").lower()
                    if fid and verdict in _ALLOWED:
                        decisions[fid]=verdict
                pending=[item for item in pending if str(item.get("findingId") or "") not in decisions]
                last_error=None if not pending else RuntimeError("verifier omitted some finding decisions")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                merge_usage(usage,getattr(exc,"llm_usage",None))
                if is_fatal_provider_error(exc):
                    raise
                last_error=exc
            if pending and attempt<critic_attempts:
                await asyncio.sleep(.35*attempt)
        if pending:
            failed_ids.update(str(item["findingId"]) for item in pending)
            warnings.append(f"Semantic evidence verifier не завершил {len(pending)} findings после точечного recovery: {last_error or 'нет решения'}")

    prepared=[dict(result) for result in results]
    by_result: dict[int,list[dict]]={}
    for finding in findings:
        by_result.setdefault(int(finding["resultIndex"]),[]).append(finding)
    for result_index, group in by_result.items():
        original=prepared[result_index]
        original_evidence=list(original.get("evidence") or [])
        confirmed_indexes=[]
        uncertain=0
        rejected=0
        for finding in group:
            fid=str(finding["findingId"])
            verdict="uncertain" if fid in failed_ids else decisions.get(fid,"uncertain")
            if verdict=="confirmed":
                confirmed_indexes.append(int(finding["evidenceIndex"]))
            elif verdict=="rejected":
                rejected+=1
            else:
                uncertain+=1
        confirmed=[{**original_evidence[i],"criticVerified":True} for i in confirmed_indexes if 0<=i<len(original_evidence)]
        if confirmed:
            prepared[result_index]={
                **original,
                "evidence":confirmed,
                "checkedBy":"llm+evidence-critic",
                "evidenceVerifier":{"confirmed":len(confirmed),"rejected":rejected,"uncertain":uncertain},
            }
        else:
            prepared[result_index]={
                **original,
                "status":"uncertain",
                "explanation":"Первичная semantic-проверка заявила нарушение, но независимый evidence verifier не подтвердил ни одного доказательства однозначно.",
                "evidence":[],
                "evidenceStatus":"rejected",
                "checkedBy":"llm+evidence-critic",
                "technicalIncomplete": bool(uncertain and any(str(item["findingId"]) in failed_ids for item in group)),
                "evidenceVerifier":{"confirmed":0,"rejected":rejected,"uncertain":uncertain},
            }
    return _apply_literal_requirements(document, rules, prepared),usage,warnings
