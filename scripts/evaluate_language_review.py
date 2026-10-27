"""Offline evaluation; run with `python -m scripts.evaluate_language_review`."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.language.evaluation import evaluate, project_prediction
from backend.app.language.validation import validate_issues

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold',type=Path,default=ROOT/'backend/tests/fixtures/language_gold.json')
    parser.add_argument('--predictions',type=Path,default=ROOT/'backend/tests/fixtures/language_baseline_predictions.json')
    parser.add_argument('--guards',action='store_true',help='Replay programmatic guards only, NOT the new LLM pipeline')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    gold=json.loads(args.gold.read_text('utf-8'))
    data=json.loads(args.predictions.read_text('utf-8'))
    if 'result' in data:
        data=data['result']
    predictions=data['issues']
    projected=[]
    for row in predictions:
        if row.get('caseId'):
            projected.append(row)
        else:
            projected.extend(value for case in gold['cases'] if case['source']!='authored_regression'
                             if (value:=project_prediction(row,case)) is not None)
    predictions=projected
    if args.guards:
        cases={c['id']:c for c in gold['cases']}
        kept=[]
        for row in predictions:
            case=cases[row['caseId']]
            fragment={'id':case['id'],'page':case['page'],'text':case['text'], 'contextBefore':'','contextAfter':''}
            output={key:row[key] for key in ('quote','replacement','category','explanation')}
            output['fragment_id']=case['id']
            findings,_=validate_issues({'reviewed_fragment_ids':[case['id']],'issues':[output]},[fragment],False)
            if not findings[0].get('disputeReason'):kept.append(row)
        predictions=kept
    scored=evaluate(gold,predictions,real_only=True)
    scored.update(mode='saved_candidates_programmatic_guards_only' if args.guards else 'saved_baseline',
                  baselineCoverage={'checkedChunks':data.get('checkedChunks'),'totalChunks':data.get('totalChunks'),'complete':data.get('complete')},
                  baselineUsage=data.get('usage'),paidRequestsMade=0)
    rendered=json.dumps(scored,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(rendered,'utf-8')
    print(rendered)


if __name__=='__main__':main()
