"""Compact, presentation-only evidence summaries; original evidence is retained unchanged."""
from __future__ import annotations
import re
from collections import defaultdict
from typing import Any

FIXES = {
    'ITMO-4-2-a4': 'Установите для страниц формат А4 (210 × 297 мм) и заново экспортируйте PDF.',
    'ITMO-4-2-color': 'Сделайте обнаруженные цветные фрагменты чёрными. Для ссылок измените цвет шрифта отдельно.',
    'ITMO-4-2-font': 'Установите Times New Roman размером не менее 12 пт для основного текста.',
    'ITMO-4-3-1': 'Проверьте сквозную нумерацию, расположение номера снизу по центру и соответствие шрифта основному тексту.',
    'ITMO-4-4-3': 'Пронумеруйте главы и подразделы арабскими цифрами без лишней точки после номера.',
    'ITMO-4-4-4': 'Приведите оформление и отступы заголовков к требованиям пункта 4.4.4.',
    'ITMO-4-5-3': 'Проверьте уникальность и формат номеров рисунков, уберите точку в конце названий, разместите подписи под рисунками по центру.',
    'ITMO-4-6-2-number': 'Пронумеруйте таблицы сквозным способом или в пределах главы, исключив повторения.',
    'ITMO-4-6-3': 'Размещайте название над таблицей слева без отступа; используйте корректный разделитель и уберите точку в конце.',
    'ITMO-4-10-1': 'Сверьте номера ссылок с библиографией: добавьте отсутствующие записи или исправьте номера ссылок.',
    'ITMO-4-10-3-order': 'Переставьте и при необходимости перенумеруйте источники в порядке первого упоминания в тексте.',
    'ITMO-4-10-3-format': 'Приведите номера и абзацные отступы библиографических записей к пункту 4.10.3.',
    'ITMO-A1-links': 'Удалите активные гиперссылки в библиографии, сохранив URL как обычный текст.',
}


def suggested_fix(rule_id: str, existing: str | None = None) -> str:
    return str(existing or FIXES.get(rule_id, ''))


def source_label(rule: dict[str, Any] | None) -> str:
    if not rule:
        return ''
    if str(rule.get('layer', '')).lower() == 'itmo_master' or str(rule.get('id', '')).startswith('ITMO-'):
        return 'Требования к ВКР ИТМО'
    return str(rule.get('sourceLabel') or '')


def _number_ranges(numbers: list[int]) -> str:
    values = sorted(set(numbers))
    if not values:
        return ''
    out = []
    start = last = values[0]
    for val in values[1:]:
        if val == last + 1:
            last = val
        else:
            out.append(str(start) if start == last else f'{start}–{last}')
            start = last = val
    out.append(str(start) if start == last else f'{start}–{last}')
    return ', '.join(out)


def evidence_groups(items: list[dict[str, Any]], samples: int = 2) -> list[dict[str, Any]]:
    """Group repeated facts conservatively by exact reason + quote, with page-size and font exceptions."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        quote = str(item.get('quote') or '').strip()
        reason = str(item.get('reason') or '').strip()
        if re.search(r'размер страницы|page size', quote, re.I):
            key = ('page-size', quote)
        elif 'Гарнитура' in reason and 'кегль' in reason:
            key = ('font', reason)
        elif 'Гарнитура номера' in reason:
            key = ('page-number-font', reason)
        elif item.get('sourceKind') == 'pdf_annotation':
            key = ('pdf-link', reason)
        elif 'Цвет фрагмента' in reason:
            key = ('colored-span', reason)
        else:
            key = ('individual', f"{item.get('blockId')}:{item.get('start')}:{item.get('end')}:{quote}:{reason}")
        groups[key].append(item)
    summary = []
    for (kind, _), members in groups.items():
        pages = [int(it['page']) for it in members if str(it.get('page') or '').isdigit()]
        summary.append({'kind': kind, 'count': len(members), 'pages': _number_ranges(pages), 'items': members[:samples], 'hidden': max(0, len(members) - samples)})
    return summary
