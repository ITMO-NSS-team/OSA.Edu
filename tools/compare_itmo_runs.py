"""Compare genuine saved reports; evidence exports stay in an ignored directory."""
import argparse
import json
from pathlib import Path


def compare(root):
    lines=['# Сравнение фактических результатов', '',
           'Страницы — физические страницы PDF. TP/FP/FN/TN требуют отдельной ручной разметки; здесь сохранены исходные результаты.', '',
           '| Работа | Правило | До → после | PDF | Конкретное наблюдение / evidence |', '|---|---|---|---|---|']
    changes=[]
    for index,label in [(1,'RU'),(2,'EN')]:
        old=json.loads((root/f'before/work_{index}/report.json').read_text())
        new=json.loads((root/f'integration/work_{index}/report.json').read_text())
        before={r['ruleId']:r for r in old['ruleResults']}
        for result in new['ruleResults']:
            prev=before[result['ruleId']]
            changes.append(dict(work=label,ruleId=result['ruleId'],before=prev,after=result))
            if result['status']!='violation' and prev['status']!='violation':
                continue
            evidence=result.get('evidence') or prev.get('evidence') or []
            for ev in evidence or [{}]:
                text=' / '.join(str(x) for x in [ev.get('check'),ev.get('reason'),ev.get('quote'),ev.get('bbox')] if x)
                text=' '.join(text.replace('|','\\|').split())
                lines.append(f"| {label} | {result['ruleId']} | {prev['status']} → {result['status']} | {ev.get('page','—')} | {text} |")
    (root/'comparison.json').write_text(json.dumps(changes,ensure_ascii=False,indent=2))
    (root/'comparison.md').write_text('\n'.join(lines)+'\n')
    print('Compared rule pairs:',len(changes))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    compare(parser.parse_args().directory)
