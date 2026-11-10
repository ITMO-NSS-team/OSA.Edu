"""Render the explicit per-rule automation audit; contains no student data."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def render():
    folder = ROOT / 'config/itmo_master'
    catalog = json.loads((folder / 'catalog.json').read_text())['rules']
    manifest = json.loads((folder / 'manifest.json').read_text())['rules']
    audit = json.loads((folder / 'automation.json').read_text())
    reports = []
    for index in (1, 2):
        path = ROOT / f'data/itmo_master/improvements/integration/work_{index}/report.json'
        reports.append({r['ruleId']: r['status'] for r in json.loads(path.read_text())['ruleResults']} if path.exists() else {})
    lines = [
        '# Аудит 77 требований ИТМО', '',
        'Источник: Требования к ВКР, версия 4.0, 2022. Категория относится к состоянию до исправлений. '
        'A — надёжная автоматизация; B — доступно по PDF, но отсутствует или ненадёжно; '
        'C — доказуемы только отдельные признаки; D — нужны внешние сведения или экспертная оценка.', '',
        'План: P0 — точные spans/evidence и семантические основания; P1 — библиография, подписи, '
        'пагинация, структура и геометрия; P2 — частичные формулы, списки, приложения; '
        'P3 — метрики, регрессии, реальные прогоны. Нормативный знаменатель всегда 77. '
        'Автоматизируемый знаменатель — все A+B+C, включая нереализованные. '
        'Для C достаточно доказанного контрпримера для FAIL, но выборка не доказывает PASS. '
        'Метрики не являются оценкой качества научного исследования.', '',
        '| Правило / пункт / PDF-страница нормы | Категория до | Реализация до → после | RU / EN после | Доступность по PDF и условия | Улучшение / граница вывода |',
        '|---|---|---|---|---|---|',
    ]
    for rule in catalog:
        rid = rule['id']; row = audit[rid]; engine = manifest[rid]['engine']['kind']
        states = ' / '.join(r.get(rid, 'не запускалось') for r in reports)
        pages = ','.join(map(str, rule['normativeReference']['pages']))
        lines.append(f"| {rid} / {rule['normativeReference']['clause']} / {pages} | {row['category']} | "
                     f"{row['before']} → {engine}: {row['implementation']} | {states} | {row['availability']}; {rule['condition']} | {row['reason']} |")
    (ROOT / 'docs/itmo_master_rules_audit.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    render()
