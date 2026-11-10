"""Explicit targeted integration recheck with the existing queue merge logic.

Previous artifacts are archived. No findings are edited or synthesized here.
Requires the caller's permission to send the specified work to its LLM provider.
"""
import argparse
import asyncio
import json
import shutil
import time
from unittest.mock import patch
from pathlib import Path

import httpx


async def recheck(args):
    from backend.app import main, store
    from backend.app.queue import _perform_check
    from backend.app.rules.registry import rules_for_profile
    rules = {r['id']: r for r in rules_for_profile('itmo_master')}
    if args.all_local:
        args.rule = [rid for rid, r in rules.items() if r['engineKind'] == 'deterministic']
    if not args.rule or any(rid not in rules for rid in args.rule):
        raise ValueError('Specify existing ITMO rules.')
    local_only = all(rules[rid]['engineKind'] == 'deterministic' for rid in args.rule)
    if not local_only and not args.allow_external_llm:
        raise ValueError('External LLM authorization is required for semantic rules.')
    root = args.directory.resolve()
    store.JOBS_FILE = root/'jobs.json'
    manifest = json.loads((root/'manifest.json').read_text())
    entry = next(j for j in manifest['jobs'] if j['index'] == args.work)
    job = await store.get_job(entry['id'])
    if not job or job.get('status') != 'completed' or job.get('profile') != 'itmo_master':
        raise ValueError('Expected a completed ITMO job in this isolated run.')
    archive = root/'archive'/f'work_{args.work}_before_recheck_{len(manifest.get("targetedRechecks", []))+1}'
    archive.parent.mkdir(exist_ok=True)
    shutil.copytree(root/f'work_{args.work}', archive)
    for file in ['jobs.json', 'manifest.json']:
        shutil.copy2(root/file, archive/file)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://integration') as client:
        health = (await client.get('/api/health')).json()
        if not local_only and not health['configured'].get(job['provider']):
            raise RuntimeError('The selected provider is not configured.')
        start = time.perf_counter()
        job = await store.update_job(job['id'], {'status':'queued_check', 'retryRuleIds':args.rule})
        if local_only:
            def forbidden(*args, **kwargs):
                raise AssertionError('Network is forbidden during a local recheck.')
            with patch('backend.app.orchestration.checker.ask_structured_json', side_effect=forbidden), \
                 patch('backend.app.orchestration.evidence_verifier.ask_structured_json', side_effect=forbidden), \
                 patch('backend.app.orchestration.candidate_checker.ask_structured_json', side_effect=forbidden):
                await _perform_check(job)
        else:
            await _perform_check(job)
        actual = await store.get_job(job['id'])
        target = root/f'work_{args.work}'
        (target/'job.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2))
        if actual['status'] != 'completed':
            raise RuntimeError(actual.get('error') or actual['status'])
        for endpoint, name in [('report.json','report.json'),('report.md','report.md'),('report.pdf','user.pdf'),('developer-report.pdf','developer.pdf')]:
            response = await client.get(f'/api/jobs/{job["id"]}/{endpoint}'); response.raise_for_status()
            (target/name).write_bytes(response.content)
        report = actual['report']
        entry.update(counts=report['counts'],reportHealth=report['reportHealth']['status'])
        manifest.setdefault('targetedRechecks', []).append(dict(work=args.work,rules=args.rule,wallMs=round((time.perf_counter()-start)*1000),archive=str(archive)))
        (root/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
        print(json.dumps({'counts':report['counts'],'coverage':report['coverageMetrics'], 'rechecked':[{'id':r['ruleId'],'status':r['status']} for r in report['ruleResults'] if r['ruleId'] in args.rule]},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--work',type=int,required=True)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--rule',action='append')
    selection.add_argument('--all-local',action='store_true')
    parser.add_argument('--allow-external-llm',action='store_true')
    asyncio.run(recheck(parser.parse_args()))
