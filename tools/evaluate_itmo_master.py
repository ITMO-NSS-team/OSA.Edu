#!/usr/bin/env python3
"""Run the real OSA.Edu checker locally, using a reviewed map, without LLM I/O.

Usage: python -m tools.evaluate_itmo_master --pdf INPUT --map REVIEWED.json
       --output data/itmo_master/work_ru
Outputs may contain private evidence. Keep them outside public Git.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch

from backend.app.defaults import DEFAULT_PROMPT
from backend.app.document.fact_store import build_document_fact_store
from backend.app.extraction import extract_document
from backend.app.llm.client import estimate_tokens
from backend.app.orchestration.checker import check_document
from backend.app.orchestration.semantic_packets import _message
from backend.app.pdf_reporting import report_to_pdf
from backend.app.reporting import make_report, report_to_markdown
from backend.app.routing.rule_router import build_routing
from backend.app.rules.registry import rules_for_profile
from backend.app.user_pdf_reporting import report_to_user_pdf
from backend.app.util import map_is_confirmed


async def evaluate(pdf: Path, reviewed_map: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    start=time.perf_counter()
    document=extract_document(pdf, layout_objects=True)
    parse_ms=round((time.perf_counter()-start)*1000)
    document['map']=json.loads(reviewed_map.read_text(encoding='utf-8'))
    if not map_is_confirmed(document['map']):
        raise ValueError('A reviewed map with explicit confirmation is required.')
    ids={b['id'] for b in document['blocks']}
    for el in document['map'].get('elements',[]):
        if el.get('startBlockId') not in ids or el.get('endBlockId') not in ids:
            raise ValueError('Map boundary does not exist in extracted PDF.')
    def reject_network(*args,**kwargs):
        raise AssertionError('External LLM I/O is forbidden in local evaluation.')
    check_start=time.perf_counter()
    # The flag is the production guard; patches make accidental regressions fail
    # loudly rather than send student text to a configured external provider.
    with patch('backend.app.orchestration.checker.ask_structured_json', side_effect=reject_network), \
         patch('backend.app.orchestration.evidence_verifier.ask_structured_json', side_effect=reject_network), \
         patch('backend.app.orchestration.candidate_checker.ask_structured_json', side_effect=reject_network):
        checked=await check_document(document=document,provider='openrouter',model='offline',
            prompt=DEFAULT_PROMPT,profile='itmo_master',additional_criteria='',allow_external_llm=False)
    check_ms=round((time.perf_counter()-check_start)*1000)
    report=make_report(checked['rules'],checked['results'],checked['warnings'],checked['llmUsage'],
        document['map'],checked['routing'],dict(profile='itmo_master',provider='offline',model='offline',
        appVersion='local',promptHash='not-sent',performance=dict(extractionMs=parse_ms,checkingMs=check_ms)))
    (output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'developer.md').write_text(report_to_markdown('Локальная проверка',report),encoding='utf-8')
    (output/'developer.pdf').write_bytes(report_to_pdf('Локальная проверка',report,profile='itmo_master'))
    (output/'user.pdf').write_bytes(report_to_user_pdf('Локальная проверка',report,profile='itmo_master'))
    # Estimate first-pass semantic packets without sending them. This is a
    # character-based estimate, not measured provider billing/token usage.
    routed=await build_routing(document=document,map_value=document['map'],rules=rules_for_profile('itmo_master'))
    fragments={f['id']:f for f in routed['fragments']};assigned={}
    for row in routed['routed']:
        if row['strategy']=='llm':
            for fid in row['fragmentIds']:assigned.setdefault(fid,[]).append(row['rule'])
    messages=[_message(document['map'],fragments[fid],rs[i:i+4],document['factStore'])
              for fid,rs in assigned.items() for i in range(0,len(rs),4)]
    metrics=dict(extractionMs=parse_ms,checkingMs=check_ms,pages=len(document['pages']),
                 blocks=len(document['blocks']),counts=report['counts'],actualLlmRequests=checked['llmUsage']['requests'],
                 estimatedSemanticPackets=len(messages),estimatedSemanticInputTokens=sum(estimate_tokens(m+'\n'+DEFAULT_PROMPT) for m in messages),
                 tokenEstimateMethod='OSA.Edu estimate_tokens; excludes map building, retries, output and evidence verification')
    (output/'metrics.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False))
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,required=True)
    parser.add_argument('--map',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    asyncio.run(evaluate(args.pdf,args.map,args.output))


if __name__=='__main__':
    main()
