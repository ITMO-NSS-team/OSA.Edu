"""Transparent rule-level coverage, distinct from object/subcheck coverage."""


def reason_for(rule, result):
    if result.get('status') in {'pass', 'violation'}:
        return 'assessed'
    if result.get('status') == 'not_applicable':
        return 'not_applicable'
    if result.get('technicalIncomplete'):
        return 'technical_failure'
    meta = rule.get('automation') or {}
    if meta.get('externalData'):
        return 'external_data'
    if rule.get('obligation') in {'recommendation', 'program'}:
        return 'advisory_or_program'
    if not meta.get('pdfAssessable'):
        return 'expert_review'
    if rule.get('engineKind') == 'unavailable':
        return 'not_implemented'
    return 'insufficient_evidence'


def coverage_metrics(rules, results):
    by_id = {r['ruleId']: r for r in results}
    eligible = [r for r in rules if (r.get('automation') or {}).get('pdfAssessable')]
    assessed = lambda rs: sum(by_id.get(r['id'], {}).get('status') in {'pass', 'violation'} for r in rs)
    reasons = {}
    for rule in rules:
        reason = reason_for(rule, by_id.get(rule['id'], {}))
        reasons.setdefault(reason, []).append(rule['id'])
    return {
        'definitionVersion': 1,
        'normative': {'assessed': assessed(rules), 'total': len(rules), 'ratio': assessed(rules)/len(rules) if rules else 0},
        'automatable': {'assessed': assessed(eligible), 'total': len(eligible), 'ratio': assessed(eligible)/len(eligible) if eligible else 0,
                        'ruleIds': [r['id'] for r in eligible], 'definition': 'Категории аудита A+B+C, включая частичные и нереализованные; решённые случаи — только PASS/FAIL.'},
        'externalData': sum(bool((r.get('automation') or {}).get('externalData')) for r in rules),
        'notImplemented': sum((r.get('automation') or {}).get('pdfAssessable', False) and r.get('engineKind') == 'unavailable' for r in rules),
        'partialImplementation': sum((r.get('automation') or {}).get('implementation') == 'partial' for r in rules),
        'uncertain': sum(r.get('status') == 'uncertain' for r in results),
        'reasonCounts': {k: len(v) for k, v in reasons.items()}, 'reasonRuleIds': reasons,
    }


def coverage_lines(report):
    value = report.get('coverageMetrics')
    if not value:
        return []
    norm = value['normative']; auto = value['automatable']; reasons = value['reasonCounts']
    return [
        f"Нормативное покрытие: {norm['assessed']}/{norm['total']} ({norm['ratio']:.1%}).",
        f"Автоматизируемые правила (A+B+C): {auto['assessed']}/{auto['total']} ({auto['ratio']:.1%}); частичные проверки не дают полного PASS.",
        f"Требуют внешних данных: {value['externalData']}; не реализовано: {value['notImplemented']}; частично реализовано: {value['partialImplementation']}; неопределённо: {value['uncertain']}.",
        f"Причины оставшихся случаев: недостаточно доказательств — {reasons.get('insufficient_evidence', 0)}; экспертная оценка — {reasons.get('expert_review', 0)}; рекомендации/решение программы — {reasons.get('advisory_or_program', 0)}; технические ошибки — {reasons.get('technical_failure', 0)}; неприменимо — {reasons.get('not_applicable', 0)}.",
    ]
