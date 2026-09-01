from __future__ import annotations

import csv
import re
from functools import lru_cache
from io import StringIO
from typing import Any

from ..config import RULES_DIR
from .manifest import load_rule_manifest, runtime_metadata

# Search/report keywords are intentionally lexical. They do not influence runtime
# engine, scope or severity; those values come only from the canonical manifest.
STOP_WORDS = {
    'это','как','что','для','или','при','также','так','все','всех','его','ее','она','они','над','под','без','после','перед',
    'если','где','когда','который','которые','данных','работы','работа','главе','глава','раздел','текст','текста','тексте',
    'быть','может','должен','должна','должны','было','были','будет','будут','чтобы','этого','этой','этих','этом','такой',
    'такие','такого','требование','совет'
}


def tokenize(value: str) -> list[str]:
    parts = re.sub(r'[^0-9A-Za-zА-Яа-яЁё]+', ' ', value.lower().replace('ё', 'е')).split()
    return list(dict.fromkeys(p for p in parts if len(p) >= 3 and p not in STOP_WORDS))


def _title(requirement: str) -> str:
    compact = re.sub(r'\s+', ' ', requirement).strip()
    sentence = re.split(r'[.!?](?:\s|$)', compact)[0] or compact
    return sentence


def _empty(value: str | None):
    value = (value or '').strip()
    return value if value and value != '–' else None


def _read_rows(path, delimiter: str) -> list[list[str]]:
    text = path.read_text(encoding='utf-8-sig')
    return list(csv.reader(StringIO(text), delimiter=delimiter, quotechar='"'))


def _catalog_item(*, rid: str, number: str, category: str, requirement: str, layer: str, source_label: str, source_line: int, correct: str | None = None, incorrect: str | None = None) -> dict[str, Any]:
    # Runtime metadata is never inferred from natural-language wording. This is
    # the key invariant of the P1 architecture: editing a sentence in CSV cannot
    # silently reroute a check or change its severity.
    meta = runtime_metadata(rid)
    item: dict[str, Any] = {
        'id': rid,
        'sourceNumber': number,
        'category': category,
        'title': _title(requirement),
        'requirement': requirement,
        'sourceLabel': source_label,
        'sourceLine': source_line,
        'layer': layer,
        **meta,
        'keywords': tokenize(f'{category} {requirement} {incorrect or ""}'),
    }
    if correct:
        item['correctExample'] = correct
    if incorrect:
        item['incorrectExample'] = incorrect
    return item


@lru_cache(maxsize=1)
def load_rule_registry() -> dict[str, list[dict[str, Any]]]:
    core_rows = _read_rows(RULES_DIR / 'core.csv', ',')
    soft_rows = _read_rows(RULES_DIR / 'soft.csv', ';')
    core: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []

    for idx, row in enumerate(core_rows[1:], start=2):
        if len(row) < 3 or not row[1].strip():
            continue
        number = row[1].strip()
        category = re.sub(r'^Группа\s+\d+\.\s*', '', row[0], flags=re.I).strip()
        requirement = row[2].strip()
        rid = 'CORE-' + re.sub(r'[^0-9A-Za-zА-Яа-яЁё]+', '-', number).strip('-')
        core.append(_catalog_item(
            rid=rid,
            number=number,
            category=category,
            requirement=requirement,
            layer='core',
            source_label='ShalytoAI_csv_rules.txt',
            source_line=idx,
            correct=_empty(row[3]) if len(row) > 3 else None,
            incorrect=_empty(row[4]) if len(row) > 4 else None,
        ))

    for idx, row in enumerate(soft_rows[1:], start=2):
        if len(row) < 3 or not row[0].strip():
            continue
        number, category, requirement = row[0].strip(), row[1].strip(), row[2].strip()
        rid = f'SOFT-{number.zfill(3)}'
        soft.append(_catalog_item(
            rid=rid,
            number=number,
            category=category,
            requirement=requirement,
            layer='soft',
            source_label='ShalytoAI_csv_rules(soft).txt',
            source_line=idx,
            incorrect=_empty(row[3]) if len(row) > 3 else None,
        ))

    catalog_ids = {item['id'] for item in [*core, *soft]}
    manifest_ids = set(load_rule_manifest().rules)
    missing = sorted(catalog_ids - manifest_ids)
    orphan = sorted(manifest_ids - catalog_ids)
    if missing or orphan:
        raise RuntimeError(
            'Rule catalog/manifest mismatch. '
            f'Missing in manifest: {missing[:10]}; orphan manifest entries: {orphan[:10]}.'
        )
    return {'core': core, 'soft': soft, 'all': [*core, *soft]}


def parse_user_rules(value: str) -> list[dict[str, Any]]:
    """User rules are explicit semantic rules, not inferred catalog rules."""
    result = []
    for idx, requirement in enumerate([x.strip() for x in value.splitlines() if len(x.strip()) >= 8][:30], start=1):
        result.append({
            'id': f'USR-{idx:02d}',
            'sourceNumber': str(idx),
            'category': 'Дополнительные требования',
            'title': _title(requirement),
            'requirement': requirement,
            'sourceLabel': 'Пользовательские требования',
            'sourceLine': idx,
            'layer': 'user',
            'mode': 'semantic',
            'scope': 'document',
            'severity': 'major',
            'weight': 3,
            'dedupKey': f'user:{idx}',
            'engineKind': 'semantic',
            'routing': {'strategy': 'llm', 'selectors': ['major_sections'], 'exhaustive': False},
            'keywords': tokenize(requirement),
        })
    return result


def rules_for_profile(profile: str, additional: str = '') -> list[dict[str, Any]]:
    registry = load_rule_registry()
    base = registry['all'] if profile == 'full' else registry['core']
    return [*base, *parse_user_rules(additional)]
