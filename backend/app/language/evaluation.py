"""Offline, deterministic scoring; never invokes an LLM or treats a mock as quality evidence."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from .validation import CATEGORIES


def project_prediction(prediction: dict, case: dict) -> dict | None:
    if prediction.get('page') != case['page']:
        return None
    quote, replacement, text = prediction['quote'], prediction['replacement'], case['text']
    if quote in text:
        return {**prediction, 'caseId': case['id']}
    if text not in quote:
        return None
    start, end = quote.index(text), quote.index(text) + len(text)
    opcodes = SequenceMatcher(None, quote, replacement, autojunk=False).get_opcodes()
    def boundary(position, right=False):
        choices = []
        for tag, a, b, c, d in opcodes:
            if tag == 'equal' and a <= position <= b:
                choices.append(c + position - a)
            elif position == a:
                choices.append(c)
            elif position == b:
                choices.append(d)
        return (max(choices) if right else min(choices)) if choices else None
    left, right = boundary(start), boundary(end, True)
    if left is None or right is None or replacement[left:right] == text:
        return None
    return {**prediction, 'caseId': case['id'], 'quote': text, 'replacement': replacement[left:right]}


def _changed_range(text: str, quote: str, replacement: str) -> tuple[int,int]:
    offset=text.index(quote)
    changes=[op for op in SequenceMatcher(None,quote,replacement,autojunk=False).get_opcodes() if op[0]!='equal']
    if not changes:return offset,offset
    return offset+changes[0][1],offset+changes[-1][2]


def evaluate(gold: dict, predictions: list[dict], *, real_only: bool=False) -> dict[str,Any]:
    cases={c['id']:c for c in gold['cases'] if not real_only or c['source']!='authored_regression'}
    totals={category:{'tp':0,'fp':0,'fn':0,'correctCorrections':0} for category in CATEGORIES if category!='style'}
    matched=set()
    records=[]
    for prediction in predictions:
        case=cases.get(prediction.get('caseId'))
        if not case:continue
        category=prediction['category']
        if category not in totals:continue
        quote,fix=prediction['quote'],prediction['replacement']
        candidates=[]
        if quote in case['text']:
            start,end=_changed_range(case['text'],quote,fix)
            for expected in case['expected']:
                if expected['category']!=category or expected['id'] in matched:continue
                for alternative in expected['allowedReplacements']:
                    a,b=_changed_range(case['text'],expected['quote'],alternative)
                    if max(start,a)<min(end,b) or start==a or end==b:
                        candidates.append(expected);break
        if not candidates:
            totals[category]['fp']+=1
            records.append({'caseId':case['id'],'id':prediction.get('id'),'outcome':'fp'})
            continue
        expected=candidates[0]
        matched.add(expected['id']);totals[category]['tp']+=1
        corrected=case['text'].replace(quote,fix,1)
        acceptable=[case['text'].replace(expected['quote'],a,1) for a in expected['allowedReplacements']]
        correct=corrected in acceptable
        totals[category]['correctCorrections']+=int(correct)
        records.append({'caseId':case['id'],'id':prediction.get('id'),'outcome':'tp','correctCorrection':correct})
    for case in cases.values():
        for expected in case['expected']:
            if expected['id'] not in matched:totals[expected['category']]['fn']+=1
    def metrics(row):
        tp,fp,fn=row['tp'],row['fp'],row['fn']
        return {**row,'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,
                'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,
                'correctionAccuracy':row['correctCorrections']/tp if tp else None}
    overall={key:sum(row[key] for row in totals.values()) for key in ('tp','fp','fn','correctCorrections')}
    return {'scope':'Annotated excerpts only; exact allowed replacements. Not whole-document or independent live-model quality.',
            'cases':len(cases),'overall':metrics(overall),'categories':{key:metrics(row) for key,row in totals.items()},'records':records}
