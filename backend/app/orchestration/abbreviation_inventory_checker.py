from __future__ import annotations

"""Fact-first abbreviation map over a high-recall Python inventory.

3.9.5-rc2 policy:
- Python enumerates abbreviation-like lexical candidates and owns document scope,
  content roles and grounded evidence.
- The LLM does *not* decide CORE-4/CORE-12 verdicts. It builds one factual map for
  every candidate: semantic entity kind, normative class, first-use expansion
  facts and document-local explanation facts.
- Python validates that map and applies declarative rule contracts from the
  canonical ``config/rule-manifest.json``.
- The logical map may be built in bounded packets for reliability. Missing rows
  receive targeted recovery only; unresolved facts become ``uncertain`` rather
  than false violations or false passes.

This mirrors the Document Map architecture: LLM identifies grounded facts;
Python enforces normative contracts.
"""

import asyncio
import json
import os
from functools import lru_cache
from typing import Any

from ..checking.abbreviations import build_llm_abbreviation_inventory
from ..llm.client import ask_structured_json, is_fatal_provider_error, salvage_json_objects
from ..util import empty_usage, merge_usage
from ..rules.manifest import load_rule_manifest, manifest_entry
from ..document.fact_store import abbreviation_is_listed

ABBREVIATION_RULE_IDS = tuple(
    rule_id for rule_id, entry in load_rule_manifest().rules.items()
    if entry.engine.kind.value == "abbreviation_fact_map"
)
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
    "unit_or_symbol",
    "quoted_or_code_token",
    "ordinary_text",
    "uncertain",
}
_ALLOWED_NORMATIVE_CLASSES = {
    "abbreviation",
    "proper_name",
    "identifier_or_symbol",
    "ordinary_text",
    "uncertain",
}
_ALLOWED_FACT_VALUES = {"yes", "no", "uncertain", "not_applicable"}
_FACT_FIELDS = (
    "isForeignAbbreviation",
    "firstUseHasRussianFullTermBefore",
    "hasExplanationAnywhere",
    "hasRussianExplanationAnywhere",
)


@lru_cache(maxsize=1)
def _rule_contracts() -> dict[str, dict[str, Any]]:
    contracts: dict[str, dict[str, Any]] = {}
    for rule_id, entry in load_rule_manifest().rules.items():
        if entry.engine.kind.value != "abbreviation_fact_map":
            continue
        payload = entry.engine.model_dump(exclude_none=True).get("contract")
        if not isinstance(payload, dict):
            raise RuntimeError(f"Abbreviation contract is missing in rule manifest for {rule_id}")
        contracts[rule_id] = dict(payload)
    return contracts


def _report_policy(rule_id: str) -> dict[str, Any]:
    entry = manifest_entry(rule_id)
    if entry is None:
        return {}
    payload = entry.engine.model_dump(exclude_none=True).get("reportPolicy")
    return dict(payload) if isinstance(payload, dict) else {}


def _small_evidence(value: dict | None) -> dict | None:
    if not isinstance(value, dict) or not value:
        return None
    return {
        "blockId": str(value.get("blockId") or ""),
        "location": str(value.get("location") or ""),
        **({"page": value.get("page")} if value.get("page") is not None else {}),
        **({"contentRole": str(value.get("contentRole"))} if value.get("contentRole") else {}),
        **({"definition": " ".join(str(value.get("definition") or "").split())[:260]} if value.get("definition") else {}),
        "quote": " ".join(str(value.get("quote") or "").split())[:420],
    }


def _prompt_inventory(inventory: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in inventory:
        rows.append({
            "id": item["candidateId"],
            "token": item["term"],
            "occurrenceCount": int(item.get("occurrenceCount") or 0),
            "contentRoles": list(item.get("contentRoles") or []),
            "contextLanguage": str(item.get("contextLanguage") or "unknown"),
            "firstUse": _small_evidence(item.get("firstUse")),
            "contextUses": [x for x in (_small_evidence(ev) for ev in item.get("contextUses") or []) if x],
            "headingUses": [x for x in (_small_evidence(ev) for ev in item.get("headingUses") or []) if x],
            "listedDefinitions": [x for x in (_small_evidence(ev) for ev in item.get("listedDefinitions") or []) if x],
        })
    return rows


def build_abbreviation_fact_map_message(inventory: list[dict], *, recovery: bool = False) -> str:
    prefix = "ТОЧЕЧНОЕ ВОССТАНОВЛЕНИЕ" if recovery else "ПОСТРОЕНИЕ"
    return f'''{prefix} КАРТЫ ОБОЗНАЧЕНИЙ ДОКУМЕНТА.

Python выполнил HIGH-RECALL поиск и передал abbreviation-like кандидаты. Список намеренно широкий: в нём могут быть настоящие сокращения, официальные названия моделей/датасетов/ресурсов, методы, метрики, формульные идентификаторы, единицы, фрагменты кода и обычные слова.

Твоя задача — НЕ проверять правила и НЕ выдавать pass/violation. Для каждого переданного id построй только ФАКТЫ по предоставленному контексту. Не ищи новые токены и не используй внешние знания.

Для каждого кандидата определи:
1. entityKind — семантический тип сущности.
2. normativeClass — как token функционирует именно в этом документе:
   - abbreviation: сокращённая форма термина, которую нормативно имеет смысл раскрывать;
   - proper_name: самостоятельное официальное/собственное имя модели, датасета, метода, продукта, ресурса и т.п.;
   - identifier_or_symbol: формульный/кодовый идентификатор, единица, технический символ;
   - ordinary_text: обычное слово/фрагмент текста;
   - uncertain: контекста недостаточно.
   Не делай token abbreviation только из-за верхнего регистра или латиницы. entityKind и normativeClass независимы: например метрика может по контексту быть как настоящей аббревиатурой, так и самостоятельным обозначением.
3. isForeignAbbreviation — yes/no только если normativeClass=abbreviation; иначе not_applicable. Под foreign понимается иностранная аббревиатура в локальном авторском контексте.
4. firstUseHasRussianFullTermBefore — есть ли в firstUse перед token полный русский термин, после которого token дан как сокращение (обычно в скобках). Оценивай только grounded firstUse. Если candidate не abbreviation или нет авторского содержательного firstUse — not_applicable. Если контекст не позволяет решить — uncertain.
5. hasExplanationAnywhere — есть ли среди firstUse/contextUses/listedDefinitions явная document-grounded расшифровка/определение token хотя бы на одном языке. Не засчитывай внешнее общеизвестное значение token.
6. hasRussianExplanationAnywhere — есть ли среди тех же переданных grounded контекстов явный русский полный термин/перевод. Английская расшифровка без русского смысла = no. Для не-abbreviation — not_applicable.

Контекстные правила:
- listedDefinitions — найденные Python записи из собственного списка сокращений/определений документа и являются сильным grounded evidence, но не меняют факт первого употребления.
- headingUses сообщает только о реальных title/TOC/heading употреблениях; не делай из наличия headingUses нормативный verdict.
- formula_like/table_like/code_or_prompt без отдельного терминологического narrative-употребления обычно указывает на identifier_or_symbol, но решение принимай по переданному контексту.
- Если данных недостаточно, используй uncertain. Не додумывай расшифровки.
- Просмотри ВСЕ переданные id и верни ровно одну строку на каждый id.

CANDIDATES:
{json.dumps(_prompt_inventory(inventory), ensure_ascii=False, separators=(',', ':'))}

Верни ТОЛЬКО JSON:
{{"entities":[{{"id":"<candidate-id>","entityKind":"abbreviation|method_or_algorithm|model_name|dataset_name|named_resource|metric_or_measure|format_or_protocol|identifier_or_code|unit_or_symbol|quoted_or_code_token|ordinary_text|uncertain","normativeClass":"abbreviation|proper_name|identifier_or_symbol|ordinary_text|uncertain","isForeignAbbreviation":"yes|no|uncertain|not_applicable","firstUseHasRussianFullTermBefore":"yes|no|uncertain|not_applicable","hasExplanationAnywhere":"yes|no|uncertain|not_applicable","hasRussianExplanationAnywhere":"yes|no|uncertain|not_applicable","reason":"очень кратко, только по переданным фактам"}}]}}
'''


# Backward-compatible name for callers/tests outside this archive. The function
# now builds a fact-map prompt and intentionally ignores rule verdicts.
def build_abbreviation_llm_message(rules: list[dict], inventory: list[dict], *, recovery: bool = False) -> str:
    return build_abbreviation_fact_map_message(inventory, recovery=recovery)


def _parse_fact_rows(value: Any, allowed_ids: set[str]) -> dict[str, dict]:
    rows = value.get("entities", []) if isinstance(value, dict) else []
    out: dict[str, dict] = {}
    if not isinstance(rows, list):
        return out
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        cid = str(raw.get("id") or "").strip()
        if cid not in allowed_ids:
            continue
        entity_kind = str(raw.get("entityKind") or "uncertain").strip().lower()
        normative_class = str(raw.get("normativeClass") or "uncertain").strip().lower()
        if entity_kind not in _ALLOWED_ENTITY_TYPES or normative_class not in _ALLOWED_NORMATIVE_CLASSES:
            continue
        parsed = {
            "id": cid,
            "entityKind": entity_kind,
            "normativeClass": normative_class,
        }
        complete = True
        for field in _FACT_FIELDS:
            fact = str(raw.get(field) or "").strip().lower()
            if fact not in _ALLOWED_FACT_VALUES:
                complete = False
                break
            parsed[field] = fact
        if not complete:
            continue

        # Cross-field normalization is factual, not normative: once the LLM has
        # said this is not an abbreviation, abbreviation-only attributes cannot
        # remain affirmative by accident.
        if normative_class in {"proper_name", "identifier_or_symbol", "ordinary_text"}:
            for field in _FACT_FIELDS:
                parsed[field] = "not_applicable"
        elif normative_class == "uncertain":
            for field in _FACT_FIELDS:
                if parsed[field] == "not_applicable":
                    parsed[field] = "uncertain"

        parsed["reason"] = " ".join(str(raw.get("reason") or "").split())[:500]
        out[cid] = parsed
    return out


# Kept as a compatibility alias for internal regression imports from 3.9.3.
def _parse_rows(value: Any, allowed_ids: set[str]) -> dict[str, dict]:
    return _parse_fact_rows(value, allowed_ids)


def _candidate_facts(candidate: dict, mapped: dict | None, fact_store: dict | None = None) -> dict[str, str]:
    row = mapped or {}
    normative_class = str(row.get("normativeClass") or "uncertain")
    if normative_class == "abbreviation":
        is_abbreviation = "yes"
    elif normative_class in {"proper_name", "identifier_or_symbol", "ordinary_text"}:
        is_abbreviation = "no"
    else:
        is_abbreviation = "uncertain"
    return {
        "isAbbreviation": is_abbreviation,
        "isForeignAbbreviation": str(row.get("isForeignAbbreviation") or "uncertain"),
        "firstUseHasRussianFullTermBefore": str(row.get("firstUseHasRussianFullTermBefore") or "uncertain"),
        "hasExplanationAnywhere": str(row.get("hasExplanationAnywhere") or "uncertain"),
        "hasRussianExplanationAnywhere": str(row.get("hasRussianExplanationAnywhere") or "uncertain"),
        "hasHeadingUse": "yes" if candidate.get("headingUses") else "no",
        "contextLanguage": str(candidate.get("contextLanguage") or "unknown"),
        "listedInAbbreviationList": "yes" if abbreviation_is_listed(fact_store, str(candidate.get("term") or "")) else "no",
    }


def _condition_matches(facts: dict[str, str], condition: dict[str, Any]) -> bool:
    if not isinstance(condition, dict) or len(condition) != 1:
        return False
    fact_name, allowed = next(iter(condition.items()))
    values = {str(x) for x in (allowed if isinstance(allowed, list) else [allowed])}
    return str(facts.get(str(fact_name), "")) in values


def _evaluate_contract(rule_id: str, candidate: dict, mapped: dict | None, fact_store: dict | None = None) -> str:
    contract = _rule_contracts().get(rule_id)
    if not contract:
        return "uncertain"
    facts = _candidate_facts(candidate, mapped, fact_store)

    for condition in contract.get("notApplicableWhenAny") or []:
        if _condition_matches(facts, condition):
            return "not_applicable"
    for condition in contract.get("passWhenAny") or []:
        if _condition_matches(facts, condition):
            return "pass"
    for condition in contract.get("uncertainWhenAny") or []:
        if _condition_matches(facts, condition):
            return "uncertain"

    decision = contract.get("decision") or {}
    fact_name = str(decision.get("fact") or "")
    value = facts.get(fact_name, "")
    if value in {str(x) for x in decision.get("violation") or []}:
        return "violation"
    if value in {str(x) for x in decision.get("pass") or []}:
        return "pass"
    if value in {str(x) for x in decision.get("notApplicable") or []}:
        return "not_applicable"
    return "uncertain"


def _evidence_for_rule(candidate: dict, rule_id: str) -> list[dict]:
    first = dict(candidate.get("firstUse") or {}) if candidate.get("firstUse") else None
    headings = [dict(ev) for ev in candidate.get("headingUses") or []]
    definitions = [dict(ev) for ev in candidate.get("listedDefinitions") or []]
    evidence_kind = str((_rule_contracts().get(rule_id) or {}).get("evidence") or "first_use")
    if evidence_kind == "heading_uses":
        evidence_items = headings[:3]
    elif evidence_kind == "definition_context":
        evidence_items = ([first] if first else []) + definitions[:2]
    else:
        evidence_items = [first] if first else headings[:1]
    out: list[dict] = []
    for item in evidence_items:
        if not item:
            continue
        item["token"] = candidate.get("term")
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


def _aggregate_rule(rule: dict, inventory: list[dict], fact_map: dict[str, dict], fact_store: dict | None = None) -> dict:
    rid = str(rule.get("id") or "")
    violations: list[tuple[dict, dict]] = []
    ambiguous: list[dict] = []
    term_findings: list[dict] = []
    responded = 0
    terminal = 0

    for candidate in inventory:
        cid = str(candidate.get("candidateId") or "")
        mapped = fact_map.get(cid)
        if mapped:
            responded += 1
        status = _evaluate_contract(rid, candidate, mapped, fact_store)
        if status in {"pass", "violation", "not_applicable"}:
            terminal += 1
        else:
            ambiguous.append(candidate)
        kind = (mapped or {}).get("entityKind", "uncertain")
        facts = _candidate_facts(candidate, mapped, fact_store)
        term_findings.append({
            "term": candidate.get("term"),
            "kind": kind,
            "normativeClass": (mapped or {}).get("normativeClass", "uncertain"),
            "status": status,
            "requiresExpansion": bool(_report_policy(rid).get("requiresExpansion")),
            "requiresRussianExplanation": bool(_report_policy(rid).get("requiresRussianExplanation")),
            "contentRoles": list(candidate.get("contentRoles") or []),
            "reason": (mapped or {}).get("reason", ""),
            "factMap": facts,
            **({"firstUse": candidate.get("firstUse")} if candidate.get("firstUse") else {}),
        })
        if status == "violation":
            violations.append((candidate, mapped or {}))

    coverage = _coverage(len(inventory), terminal, len(inventory) - terminal, responded)
    evidence_items: list[dict] = []
    finding_ids: list[str] = []
    for candidate, _mapped in violations:
        evidence_items.extend(_evidence_for_rule(candidate, rid))
        finding_ids.append(f"abbr-map:{rid}:{candidate.get('candidateId')}")
    unique: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    for ev in evidence_items:
        key = (str(ev.get("blockId") or ""), str(ev.get("token") or ""), str(ev.get("quote") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ev)
    evidence_items = unique[:20]

    if violations:
        terms = list(dict.fromkeys(str(candidate.get("term")) for candidate, _ in violations))
        status = "violation"
        policy = _report_policy(rid)
        template = str(policy.get("violationExplanation") or "По карте обозначений подтверждены нарушения для: {terms}.")
        explanation = template.format(terms=", ".join(terms))
        fix = str(policy.get("fix") or "") or None
    elif ambiguous:
        status = "uncertain"
        terms = list(dict.fromkeys(str(candidate.get("term")) for candidate in ambiguous))
        explanation = "Карта обозначений построена не полностью однозначно; ручной проверки требуют: " + ", ".join(terms[:20]) + "."
        fix = None
    else:
        status = "pass"
        explanation = "Карта обозначений построена; Python не нашёл подтверждённых нарушений этого правила."
        fix = None

    result = {
        "ruleId": rid,
        "status": status,
        "severity": rule.get("severity", "major"),
        "explanation": explanation,
        "confidence": 1 if status in {"pass", "violation"} else 0,
        "evidence": evidence_items,
        "evidenceStatus": "verified" if evidence_items else "not_required",
        "checkedBy": "abbreviation-fact-map+python",
        "coverage": coverage,
        "manualReviewCount": len(ambiguous),
        "termFindings": term_findings,
    }
    if fix:
        result["fix"] = fix
    if finding_ids:
        result["findingIds"] = finding_ids
    return result


def _technical_failure(rule: dict, message: str, candidate_count: int) -> dict:
    return {
        "ruleId": rule.get("id"),
        "status": "not_checked",
        "severity": rule.get("severity", "major"),
        "explanation": message,
        "confidence": 0,
        "evidence": [],
        "evidenceStatus": "not_required",
        "checkedBy": "abbreviation-fact-map+python",
        "technicalIncomplete": True,
        "coverage": _coverage(candidate_count, 0, candidate_count, 0),
    }


def _chunks(items: list[dict], size: int) -> list[list[dict]]:
    return [items[offset:offset + size] for offset in range(0, len(items), size)]


async def _request_fact_chunks(
    *,
    chunks: list[list[dict]],
    provider: str,
    model: str,
    system_prompt: str,
    recovery: bool,
    concurrency: int,
) -> list[tuple[list[dict], dict | None, BaseException | None]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(chunk: list[dict]) -> tuple[list[dict], dict | None, BaseException | None]:
        async with semaphore:
            try:
                response = await ask_structured_json(
                    provider=provider,
                    model=model,
                    system_prompt=system_prompt,
                    user_message=build_abbreviation_fact_map_message(chunk, recovery=recovery),
                    operation="check",
                    packets=1,
                    candidates=len(chunk),
                    max_completion_tokens=max(2200, min(8000, 1200 + len(chunk) * 105)),
                )
                return chunk, response, None
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # preserve provider metadata for diagnostics
                raw = str(getattr(exc, "raw_response", "") or "")
                if raw:
                    salvaged = salvage_json_objects(raw, required_key="id")
                    ids = {str(item["candidateId"]) for item in chunk}
                    parsed = _parse_fact_rows({"entities": salvaged}, ids)
                    if parsed:
                        return chunk, {
                            "value": {"entities": list(parsed.values())},
                            "usage": getattr(exc, "llm_usage", None) or empty_usage(),
                            "salvagedRows": len(parsed),
                        }, exc
                return chunk, None, exc

    return await asyncio.gather(*(one(chunk) for chunk in chunks))


async def execute_abbreviation_inventory_check(
    *,
    document: dict,
    rules: list[dict],
    provider: str,
    model: str,
    system_prompt: str,
    fact_store: dict | None = None,
) -> tuple[list[dict], dict, list[str]]:
    usage = empty_usage()
    usage["abbreviationMode"] = "llm-fact-map-high-recall"
    usage["abbreviationFactMapVersion"] = 2
    warnings: list[str] = []
    relevant = [rule for rule in rules if str(rule.get("id")) in ABBREVIATION_RULE_IDS]
    if not relevant:
        return [], usage, warnings

    # Fail early on a malformed contract file rather than silently changing rule
    # semantics in production.
    contracts = _rule_contracts()
    missing_contracts = [str(rule.get("id")) for rule in relevant if str(rule.get("id")) not in contracts]
    if missing_contracts:
        detail = "Не найдены контракты карты обозначений: " + ", ".join(missing_contracts)
        return [_technical_failure(rule, detail, 0) for rule in relevant], usage, [detail]

    inventory = build_llm_abbreviation_inventory(document)
    usage["abbreviationCandidateCount"] = len(inventory)
    if not inventory:
        return [_aggregate_rule(rule, [], {}, fact_store) for rule in relevant], usage, warnings

    fact_map: dict[str, dict] = {}
    all_ids = {str(item["candidateId"]) for item in inventory}
    primary_errors: list[BaseException] = []
    chunk_size = max(1, int(os.getenv("ABBREVIATION_FACT_MAP_CHUNK_SIZE", "10") or 10))
    concurrency = max(1, int(os.getenv("ABBREVIATION_FACT_MAP_CONCURRENCY", "2") or 2))
    primary_chunks = _chunks(inventory, chunk_size)
    usage["abbreviationFactMapPackets"] = len(primary_chunks)

    primary_results = await _request_fact_chunks(
        chunks=primary_chunks,
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        recovery=False,
        concurrency=concurrency,
    )
    for chunk, response, error in primary_results:
        if response is not None:
            merge_usage(usage, response.get("usage"))
            usage["abbreviationSalvagedRows"] = int(usage.get("abbreviationSalvagedRows", 0)) + int(response.get("salvagedRows", 0) or 0)
            ids = {str(item["candidateId"]) for item in chunk}
            fact_map.update(_parse_fact_rows(response.get("value"), ids))
            continue
        if error is not None:
            merge_usage(usage, getattr(error, "llm_usage", None))
            if is_fatal_provider_error(error):
                raise error
            primary_errors.append(error)

    missing = [item for item in inventory if str(item["candidateId"]) not in fact_map]
    recovery_calls = 0
    if missing:
        recovery_size = max(1, int(os.getenv(
            "ABBREVIATION_FACT_MAP_RECOVERY_CHUNK_SIZE",
            os.getenv("ABBREVIATION_LLM_RECOVERY_CHUNK_SIZE", "5"),
        ) or 5))
        max_rounds = max(1, int(os.getenv(
            "ABBREVIATION_FACT_MAP_RECOVERY_ROUNDS",
            os.getenv("ABBREVIATION_LLM_RECOVERY_ROUNDS", "2"),
        ) or 2))
        recovery_concurrency = max(1, min(concurrency, 2))
        recovery_errors: list[BaseException] = []
        for _round in range(max_rounds):
            if not missing:
                break
            recovery_chunks = _chunks(missing, recovery_size)
            results = await _request_fact_chunks(
                chunks=recovery_chunks,
                provider=provider,
                model=model,
                system_prompt=system_prompt,
                recovery=True,
                concurrency=recovery_concurrency,
            )
            recovery_calls += len(recovery_chunks)
            for chunk, response, error in results:
                if response is not None:
                    merge_usage(usage, response.get("usage"))
                    usage["abbreviationSalvagedRows"] = int(usage.get("abbreviationSalvagedRows", 0)) + int(response.get("salvagedRows", 0) or 0)
                    ids = {str(item["candidateId"]) for item in chunk}
                    fact_map.update(_parse_fact_rows(response.get("value"), ids))
                elif error is not None:
                    merge_usage(usage, getattr(error, "llm_usage", None))
                    if is_fatal_provider_error(error):
                        raise error
                    recovery_errors.append(error)
            missing = [item for item in missing if str(item["candidateId"]) not in fact_map]

        if missing and recovery_errors:
            warnings.append(f"Recovery карты обозначений не завершил {len(missing)} кандидатов: {recovery_errors[-1]}")

    usage["abbreviationRecoveryRequests"] = recovery_calls
    usage["abbreviationResolvedCandidates"] = len(fact_map)
    usage["abbreviationUnresolvedCandidates"] = max(0, len(inventory) - len(fact_map))

    if not fact_map:
        detail = f"LLM не вернула ни одной строки карты для {len(inventory)} найденных обозначений"
        if primary_errors:
            detail += f": {primary_errors[-1]}"
        return [_technical_failure(rule, detail, len(inventory)) for rule in relevant], usage, warnings

    if missing:
        warnings.append(
            f"Карта обозначений не содержит {len(missing)} из {len(inventory)} кандидатов; они оставлены для ручной проверки."
        )
    # Deliberately do not surface stale primary errors when targeted recovery has
    # reconstructed every requested fact-map row.
    return [_aggregate_rule(rule, inventory, fact_map, fact_store) for rule in relevant], usage, warnings
