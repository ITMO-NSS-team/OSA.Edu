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
            m=re.match(r'^\s*(\d{1,3})[.)]\s+',b.get('text',''))
            if m: nums.append((int(m.group(1)),b))
        if not nums: return result(rule,'uncertain','Нумерацию списка литературы не удалось распознать.')
        expected=list(range(nums[0][0],nums[0][0]+len(nums)))
        actual=[n for n,_ in nums]
        if actual!=expected:
            bad=[evidence(b,b.get('text','')[:450]) for n,b in nums if n not in expected or actual.index(n)!=expected.index(n)][:15]
            return result(rule,'violation','В нумерации списка литературы обнаружены пропуски или нарушение последовательности.',bad,1,'detector','Исправить последовательность номеров источников.')
        return result(rule,'pass','Распознанная нумерация списка литературы последовательна.',confidence=1)
    if rid=='CORE-18':
        ev=[]
        for b in blocks:
            text=b.get('text','')
            for m in re.finditer(r'\b(?:URL|doi|DOI)\b|https?://',text,re.I):
                if not re.search(r'(?:дата\s+обращения|accessed)',text,re.I): ev.append(evidence(b,contextual(text,m.start(),len(m.group(0)))))
        return result(rule,'violation','Для электронного источника не найдена дата обращения.',dedupe_evidence(ev)[:15],1,'detector','Добавить дату обращения к электронному ресурсу.') if ev else result(rule,'pass','Для распознанных электронных источников явное отсутствие даты обращения не обнаружено.',confidence=1)
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
