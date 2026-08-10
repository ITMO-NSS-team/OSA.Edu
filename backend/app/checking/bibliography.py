from __future__ import annotations
import regex as re
from .common import evidence, result, dedupe_evidence, contextual


def run_bibliography_rule(rule:dict,document:dict)->dict:
    blocks=document.get('fields',{}).get('bibliographyBlocks',[])
    if not blocks: return result(rule,'uncertain','Список литературы не удалось надёжно распознать.')
    rid=rule['id']
    if rid=='CORE-9-1':
        nums=[]
        for b in blocks:
            text=b.get('text','')
            # A bibliography entry number must begin a plausible entry.  A PDF
            # line such as ``35. — Curran Associates, Inc., 2022`` is usually a
            # continuation containing a volume number (``Т. 35``), not source 35.
            m=re.match(r'^\s*(\d{1,3})[.)]\s+(?![—–-])(?=\S)',text)
            if m: nums.append((int(m.group(1)),b))
        if not nums: return result(rule,'uncertain','Нумерацию списка литературы не удалось распознать.')
        # A confirmed bibliography range may contain additional numbered reference
        # lists in appendices. Treat a reset (e.g. 78 -> 1) as a new list instead
        # of declaring the entire document non-sequential. A genuine gap inside
        # one run (e.g. 12 -> 14) remains a violation.
        runs=[]
        current=[]
        for number,block in nums:
            if current and number <= current[-1][0]:
                runs.append(current)
                current=[]
            current.append((number,block))
        if current:
            runs.append(current)

        bad=[]
        for run in runs:
            expected=list(range(run[0][0],run[0][0]+len(run)))
            for index,(number,block) in enumerate(run):
                if number!=expected[index]:
                    bad.append(evidence(block,block.get('text','')[:450]))
        if bad:
            return result(rule,'violation','В нумерации одного из распознанных списков литературы обнаружены пропуски или нарушение последовательности.',bad[:15],1,'detector','Исправить последовательность номеров источников.')
        return result(rule,'pass',f'Распознанная нумерация последовательна во всех найденных библиографических списках ({len(runs)}).',confidence=1)
    if rid=='CORE-18':
        ev=[]
        # CORE-18 is about author-name order only.  Do not mix it with access-date
        # requirements.  Flag high-confidence forms such as ``M. Chen`` or
        # ``M. H. Lees``; full names without initials are not evidence here.
        initials_before_surname=re.compile(
            r'(?<![\p{L}.])(?:[А-ЯЁA-Z]\.\s*){1,3}[А-ЯЁA-Z][\p{L}`’\'’-]{1,}(?=\s*(?:[,;/]|\[|$))'
        )
        for b in blocks:
            text=b.get('text','')
            for m in initials_before_surname.finditer(text):
                ev.append(evidence(b,contextual(text,m.start(),len(m.group(0)))))
        if ev:
            return result(rule,'violation','Обнаружены авторы, у которых инициалы стоят перед фамилией.',dedupe_evidence(ev)[:15],1,'detector','Оформить имена авторов единообразно: «Фамилия И. О.».')
        return result(rule,'pass','Высокоуверенных случаев расположения инициалов перед фамилией в распознанных библиографических записях не обнаружено.',confidence=1)
    # deterministic bibliographic formatting patterns
    ev=[]
    patterns=[]
    if rid=='CORE-9-2': patterns=[r'\bISBN\b',r'\bed\.\s+by\b',r'\s&\s',r'\b[A-ZА-ЯЁ][\p{L}-]+,\s+[A-ZА-ЯЁ]\.']
    elif rid=='CORE-9-3': patterns=[r'\bp\.\s*\d+\s*[-–—]\s*\d+\b']
    for b in blocks:
        for p in patterns:
            for m in re.finditer(p,b.get('text',''),re.I): ev.append(evidence(b,contextual(b['text'],m.start(),len(m.group(0)))))
    if ev: return result(rule,'violation',rule.get('requirement','Обнаружено нарушение оформления библиографии.'),dedupe_evidence(ev)[:15],1,'detector','Унифицировать оформление библиографической записи.')
    return result(rule,'pass','Явных нарушений данного библиографического правила не обнаружено.',confidence=1)
