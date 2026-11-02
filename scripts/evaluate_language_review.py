"""Offline evaluation of saved predictions against an explicitly supplied gold file.

Run with `python -m scripts.evaluate_language_review --gold PATH --predictions PATH`.
No evaluation corpus or saved baseline is distributed with the repository.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.language.evaluation import evaluate, project_prediction
from backend.app.language.validation import validate_issues

def _read_json(parser: argparse.ArgumentParser, path: Path, option: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(f'{option}: cannot read JSON file {path}: {exc}')
    if not isinstance(value, dict):
        parser.error(f'{option}: {path} must contain a JSON object')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gold',type=Path,required=True,help='Existing annotated gold JSON (cases array)')
    parser.add_argument('--predictions',type=Path,required=True,help='Existing saved predictions JSON (issues or result.issues)')
    parser.add_argument('--guards',action='store_true',help='Replay programmatic guards only, NOT the new LLM pipeline')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    gold=_read_json(parser,args.gold,'--gold')
    data=_read_json(parser,args.predictions,'--predictions')
    if not isinstance(gold.get('cases'),list):
        parser.error(f'--gold: {args.gold} must contain a cases array')
    if 'result' in data:
        data=data['result']
    if not isinstance(data,dict) or not isinstance(data.get('issues'),list):
        parser.error(f'--predictions: {args.predictions} must contain an issues or result.issues array')
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
    if args.output:
        try:
            args.output.write_text(rendered,'utf-8')
        except OSError as exc:
            parser.error(f'--output: cannot write {args.output}: {exc}')
    print(rendered)


if __name__=='__main__':main()
