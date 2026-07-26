from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import APP_VERSION
from .util import normalized_quote

SCORE_FAMILIES = [
    ["CORE-5-4", "CORE-7-2", "CORE-19"],
    ["CORE-4-1", "CORE-4-3", "CORE-12"],
]
STATUS_ORDER = ["violation", "pass", "uncertain", "not_checked", "not_applicable"]


def _link_related_violations(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence_groups: dict[str, set[str]] = defaultdict(set)
    finding_groups: dict[str, set[str]] = defaultdict(set)
    for result in results:
        if result.get("status") != "violation":
            continue
        for evidence in result.get("evidence", []) or []:
            key = f"{evidence.get('blockId','')}|{normalized_quote(str(evidence.get('quote','')))}"
            evidence_groups[key].add(str(result.get("ruleId", "")))
        for finding_id in result.get("findingIds", []) or []:
            finding_groups[str(finding_id)].add(str(result.get("ruleId", "")))

    related: dict[str, set[str]] = defaultdict(set)
    for ids in [*evidence_groups.values(), *finding_groups.values()]:
        if len(ids) <= 1:
            continue
        for rule_id in ids:
            related[rule_id].update(x for x in ids if x != rule_id)

    prepared: list[dict[str, Any]] = []
    for result in results:
        item = dict(result)
        ids = related.get(str(result.get("ruleId", "")))
        if ids:
            item["relatedRuleIds"] = sorted(ids)
        prepared.append(item)
    return prepared


def _score_group_key(rule: dict[str, Any]) -> str:
    for family in SCORE_FAMILIES:
        if rule.get("id") in family:
            return "family:" + "|".join(family)
    if rule.get("layer") == "user":
        return f"rule:{rule.get('id')}"
    if rule.get("detectorId"):
        return f"detector:{rule.get('detectorId')}"
    return f"rule:{rule.get('id')}"


def _merge_status(left: str, right: str) -> str:
    rank = {"violation": 5, "pass": 4, "uncertain": 3, "not_checked": 2, "not_applicable": 1}
    return right if rank.get(right, 0) > rank.get(left, 0) else left


def _build_score_groups(rules: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result_map = {x.get("ruleId"): x for x in results}
    groups: dict[str, dict[str, Any]] = {}
    for rule in rules:
        key = _score_group_key(rule)
        status = (result_map.get(rule.get("id")) or {}).get("status", "not_checked")
        if key not in groups:
            groups[key] = {"status": status, "weight": float(rule.get("weight", 1) or 1)}
        else:
            groups[key]["weight"] = max(groups[key]["weight"], float(rule.get("weight", 1) or 1))
            groups[key]["status"] = _merge_status(groups[key]["status"], status)
    return list(groups.values())


def make_report(
    rules: list[dict[str, Any]],
    results: list[dict[str, Any]],
    warnings: list[str],
    llm_usage: dict[str, Any],
    document_map: dict[str, Any] | None,
    routing: dict[str, Any],
    technical_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prepared = _link_related_violations(results)
    count = lambda status: sum(1 for x in prepared if x.get("status") == status)
    violations = [x for x in prepared if x.get("status") == "violation"]
    applicable = [x for x in prepared if x.get("status") != "not_applicable"]
    checked = [x for x in applicable if x.get("status") in {"pass", "violation"}]
    coverage = len(checked) / len(applicable) if applicable else 0.0

    candidate_rows = [x["coverage"] for x in prepared if isinstance(x.get("coverage"), dict)]
    total_candidates = sum(int(x.get("candidateCount", 0) or 0) for x in candidate_rows)
    checked_candidates = sum(int(x.get("checkedCandidateCount", 0) or 0) for x in candidate_rows)
    candidate_coverage = min(1.0, checked_candidates / total_candidates) if total_candidates else 1.0

    groups = _build_score_groups(rules, prepared)
    pass_weight = sum(x["weight"] for x in groups if x["status"] == "pass")
    violation_weight = sum(x["weight"] for x in groups if x["status"] == "violation")
    score = None if coverage < 0.6 or pass_weight + violation_weight == 0 else round(100 * pass_weight / (pass_weight + violation_weight))

    counts = {
        "critical": sum(1 for x in violations if x.get("severity") == "critical"),
        "major": sum(1 for x in violations if x.get("severity") == "major"),
        "minor": sum(1 for x in violations if x.get("severity") == "minor"),
        "info": sum(1 for x in violations if x.get("severity") == "info"),
        "pass": count("pass"),
        "violation": count("violation"),
        "uncertain": count("uncertain"),
        "notApplicable": count("not_applicable"),
        "notChecked": count("not_checked"),
    }
    if score is None:
        summary = f"Проверено {len(checked)} из {len(applicable)} применимых правил. Покрытие недостаточно для итоговой оценки."
    else:
        summary = (
            f"Проверено {len(checked)} из {len(applicable)} применимых правил. "
            f"Нарушено: {counts['violation']}; неопределённо: {counts['uncertain']}; "
            f"не обработано: {counts['notChecked']}."
        )

    technical_input = technical_input or {}
    technical = {
        "appVersion": technical_input.get("appVersion") or APP_VERSION,
        "provider": technical_input.get("provider") or (document_map or {}).get("provider") or "openrouter",
        "model": technical_input.get("model") or (document_map or {}).get("model") or "unknown",
        "promptHash": technical_input.get("promptHash") or "unknown",
        "mapPromptHash": technical_input.get("mapPromptHash") or (document_map or {}).get("promptHash"),
    }
    catalog_keys = ["id", "category", "title", "requirement", "sourceLabel", "sourceLine", "mode", "scope", "severity", "correctExample", "incorrectExample"]
    return {
        "ruleResults": prepared,
        "documentMap": document_map,
        "ruleCatalog": [{k: rule.get(k) for k in catalog_keys if rule.get(k) is not None} for rule in rules],
        "summary": summary,
        "score": score,
        "scoreIsProvisional": score is not None and coverage < 0.9,
        "coverage": coverage,
        "candidateCoverage": candidate_coverage,
        "counts": counts,
        "checkedRules": len(checked),
        "totalRules": len(rules),
        "warnings": warnings,
        "llmUsage": llm_usage,
        "technical": technical,
        "ruleStats": [{"status": status, "count": count(status)} for status in STATUS_ORDER],
        "routing": routing,
    }


def _status_heading(value: str) -> str:
    return {
        "violation": "Нарушено",
        "pass": "Выполнено",
        "uncertain": "Неопределённо",
        "not_checked": "Не обработано",
        "not_applicable": "Неприменимо",
    }.get(value, value)


def _evidence_status(value: str | None) -> str:
    return {
        "verified": "цитаты подтверждены по исходным блокам",
        "coverage_verified": "отсутствие подтверждено полным просмотром назначенной области",
        "rejected": "заявленное нарушение не подтверждено допустимой цитатой или полной матрицей",
        "not_required": "доказательство не требовалось",
    }.get(value or "not_required", "доказательство не требовалось")


def _matrix_status(value: str) -> str:
    return {"found": "найдено", "not_found": "не найдено", "ambiguous": "неоднозначно"}.get(value, value)


def _escape_table(value: str) -> str:
    return " ".join(str(value).replace("|", "\\|").split())


def report_to_markdown(name: str, report: dict[str, Any]) -> str:
    score = "не рассчитана" if report.get("score") is None else f"{report['score']}/100" + (" (предварительно)" if report.get("scoreIsProvisional") else "")
    catalog = {x.get("id"): x for x in report.get("ruleCatalog", [])}
    lines = [
        f"# Протокол нормоконтроля — {name}", "",
        f"**Оценка по проверенным правилам:** {score}",
        f"**Покрытие правил:** {round(float(report.get('coverage',0))*100)} % ({report.get('checkedRules',0)} проверенных из {report.get('totalRules',0)} правил профиля)",
        f"**Отправлено назначенных фрагментов:** {round(float(report.get('candidateCoverage',0))*100)} %", "",
        f"Нарушено: {report['counts']['violation']}; выполнено: {report['counts']['pass']}; неопределённо: {report['counts']['uncertain']}; не обработано: {report['counts']['notChecked']}; неприменимо: {report['counts']['notApplicable']}.", "",
        "> Оценка не является решением о допуске к защите. Смысловые замечания и исправления необходимо проверить вручную.", "",
        "## Краткий итог", str(report.get("summary", "")), "",
    ]

    document_map = report.get("documentMap")
    if document_map:
        important = {"title", "introduction", "goal", "tasks", "defense_statements", "chapter", "chapter_conclusions", "conclusion", "bibliography"}
        displayed = [x for x in document_map.get("elements", []) if x.get("type") in important]
        elements = document_map.get("elements", [])
        extraction = document_map.get("extraction", {})
        review = document_map.get("review", {})
        lines += [
            "## Структурная карта", "",
            f"- Статус: {'готова' if document_map.get('status') == 'ready' else 'частичная'}",
            f"- Подтверждена пользователем: {'да' if review.get('confirmedByUser') else 'нет'}",
            f"- Обработано блоков одним запросом: {extraction.get('processedBlocks',0)} из {extraction.get('totalBlocks',0)}",
            f"- Всего смысловых диапазонов: {len(elements)}; показано основных: {len(displayed)}",
            f"- Неоднозначных элементов: {sum(1 for x in elements if x.get('state') == 'ambiguous')}", "",
        ]
        for element in displayed:
            pages = element.get("pages", []) or []
            page_text = f" (стр. {pages[0]}–{pages[-1]})" if pages else ""
            ambiguous = " — требует проверки" if element.get("state") == "ambiguous" else ""
            lines.append(f"- **{element.get('type')} · {element.get('label')}:** {element.get('startBlockId')}…{element.get('endBlockId')}{page_text}{ambiguous}")
        issues = document_map.get("issues", []) or []
        if issues:
            lines += ["", "### Замечания к карте", "", *[f"- {x.get('message','')}" for x in issues], ""]
        else:
            lines.append("")

    for status in STATUS_ORDER:
        items = [x for x in report.get("ruleResults", []) if x.get("status") == status]
        lines += [f"## {_status_heading(status)} ({len(items)})", ""]
        for result in items:
            rule = catalog.get(result.get("ruleId"))
            lines += [f"### {result.get('ruleId')}{' — ' + str(rule.get('title')) if rule else ''}", ""]
            if rule:
                lines.append(f"- **Требование:** {rule.get('requirement','')}")
                lines.append(f"- **Источник:** {rule.get('sourceLabel','')}, строка {rule.get('sourceLine','')}")
            lines.append(f"- **Результат:** {result.get('explanation','')}")
            lines.append(f"- **Проверено:** {result.get('checkedBy','')}")
            lines.append(f"- **Проверка доказательств:** {_evidence_status(result.get('evidenceStatus'))}")
            if result.get("relatedRuleIds"):
                lines.append(f"- **Связанные правила:** {', '.join(result['relatedRuleIds'])}")
            if result.get("consistencyNotes"):
                lines.append(f"- **Проверка согласованности:** {' '.join(result['consistencyNotes'])}")
            coverage = result.get("coverage")
            if coverage:
                lines.append(f"- **Фрагменты:** {coverage.get('checkedCandidateCount',0)} из {coverage.get('candidateCount',0)}; полная область: {'да' if coverage.get('exhaustive') else 'нет'}")
            if result.get("termFindings"):
                lines.append("- **Разбор обозначений:**")
                for term in result["termFindings"]:
                    lines.append(f"  - {term.get('term')}: {term.get('kind')}; {term.get('status')}")
            if result.get("coverageMatrix"):
                lines += ["", "#### Матрица полного покрытия", "", "| Фрагмент | Проверено блоков | Полнота | Элементы |", "|---|---:|---|---|"]
                for row in result["coverageMatrix"]:
                    cell = "; ".join(f"{x.get('name')}: {_matrix_status(str(x.get('status')))}" for x in row.get("items", []))
                    lines.append(f"| {_escape_table(row.get('label',''))} | {row.get('checkedBlocks',0)}/{row.get('totalBlocks',0)} | {'полная' if row.get('complete') else 'неполная'} | {cell} |")
                lines.append("")
            if result.get("evidence"):
                lines.append("- **Доказательства:**")
                for evidence in result["evidence"]:
                    page = f", стр. {evidence.get('page')}" if evidence.get("page") else ""
                    lines.append(f"  - {evidence.get('location','')}{page}: «{evidence.get('quote','')}»")
            if result.get("fix"):
                lines.append(f"- **Исправление:** {result['fix']}")
            lines.append("")

    usage = report.get("llmUsage", {})
    technical = report.get("technical", {})
    routing = report.get("routing", {})
    lines += [
        "## Технические сведения", "",
        f"- Версия приложения: {technical.get('appVersion','')}",
        f"- Провайдер API: {technical.get('provider','')}",
        f"- Model ID: {technical.get('model','')}",
        f"- Хеш промпта проверки: {technical.get('promptHash','')}",
        f"- Хеш промпта структуры: {technical.get('mapPromptHash') or '—'}", "",
        "## Нагрузка LLM", "",
        f"- Физических запросов: {usage.get('requests',0)}",
        f"- Повторных попыток: {usage.get('retries',0)}",
        f"- Проверено пакетов: {usage.get('packets',0)}",
        f"- Передано правил/объектов: {usage.get('candidates',0)}",
        f"- Маршрутизация: {routing.get('strategy','')}; явно задано: {routing.get('explicitRules',0)}; fallback: {routing.get('fallbackRules',0)}; фрагментов: {routing.get('fragments',0)}; запросов проверки: {routing.get('checkRequests',0)}",
        f"- Оценочно входных токенов: {usage.get('estimatedInputTokens',0)}",
        f"- Ожидание rate limiter: {round(float(usage.get('rateLimitWaitMs',0))/1000)} с",
        f"- Время запросов к модели: {round(float(usage.get('requestDurationMs',0))/1000)} с", "",
    ]
    traces = usage.get("traces", []) or []
    if traces:
        lines += ["### Успешные обращения к модели", ""]
        for trace in traces:
            lines.append(f"- {trace.get('operation')}: {trace.get('provider')}/{trace.get('model')}; upstream={trace.get('providerName') or '—'}; HTTP {trace.get('httpStatus')}; compatibility={'да' if trace.get('compatibilityMode') else 'нет'}; request={trace.get('requestId') or '—'}")
        lines.append("")
    diagnostics = usage.get("diagnostics", []) or []
    if diagnostics:
        lines += ["## Диагностика API", ""]
        for item in diagnostics:
            quota = f"; quota={item.get('quotaMetric')}" if item.get("quotaMetric") else ""
            lines.append(f"- {item.get('operation')}, попытка {item.get('attempt')}, HTTP {item.get('httpStatus') or '—'}: {item.get('message')}; retry={str(bool(item.get('retryable'))).lower()}; ожидание={item.get('backoffMs',0)} мс{quota}")
        lines.append("")
    if report.get("warnings"):
        lines += ["## Ограничения проверки", "", *[f"- {x}" for x in report["warnings"]], ""]
    return "\n".join(lines)
