"""Run the real FastAPI/queue pipeline in an isolated local data directory.

External LLM calls require --allow-external-llm. No results are mocked. Existing
jobs, uploads and reports are untouched; all exports are downloaded from the
same endpoints used by the web client. The caller must authorize transmission.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx


async def run(args):
    if not args.allow_external_llm:
        raise ValueError('Explicit --allow-external-llm is required after user authorization.')
    from backend.app import extraction, main, store
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=True)
    if (output/'jobs.json').exists():
        raise ValueError('Choose a fresh output directory; previous runs are preserved.')
    # These globals belong to this one integration process. All algorithms,
    # registry, contracts, API handlers and queue workers are the application code.
    store.JOBS_FILE=output/'jobs.json'
    main.UPLOADS_DIR=output/'uploads';main.UPLOADS_DIR.mkdir()
    extraction.EXTRACTED_DIR=output/'extracted';extraction.EXTRACTED_DIR.mkdir()
    manifest=dict(profile='itmo_master',model=args.model,sourceFiles=[str(p.resolve()) for p in args.pdf],jobs=[])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://integration',timeout=60) as client:
        health=(await client.get('/api/health')).json()
        model=next((m for m in health['models'] if m['id']==args.model),None)
        if not model or not health['configured'].get(model['provider']):
            raise RuntimeError('Selected model provider is not configured.')
        response=await client.post('/api/jobs',data=dict(profile='itmo_master',model=args.model,developerMode='true'),
            files=[('files',(p.name,p.read_bytes(),'application/pdf')) for p in args.pdf])
        response.raise_for_status();created=response.json()
        seen={};pending={j['id']:i+1 for i,j in enumerate(created)}
        deadline=time.monotonic()+args.timeout
        while pending:
            if time.monotonic()>deadline:
                raise TimeoutError('Integration timed out; preserved jobs.json contains the actual state.')
            response=await client.get('/api/jobs');response.raise_for_status()
            for job in response.json():
                jid=job['id']
                if jid not in pending:continue
                state=(job['status'],job.get('progress'))
                if seen.get(jid)!=state:
                    print(f"work_{pending[jid]} {state[0]} {state[1]}%",flush=True);seen[jid]=state
                if state[0] not in {'completed','failed','cancelled','awaiting_review'}:continue
                index=pending.pop(jid);target=output/f'work_{index}';target.mkdir()
                (target/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
                item=dict(index=index,id=jid,status=state[0],error=job.get('error'),artifacts={})
                if state[0]=='completed':
                    for endpoint,name in [('report.json','report.json'),('report.md','report.md'),('report.pdf','user.pdf'),('developer-report.pdf','developer.pdf')]:
                        artifact=await client.get(f'/api/jobs/{jid}/{endpoint}');artifact.raise_for_status()
                        (target/name).write_bytes(artifact.content);item['artifacts'][name]=str(target/name)
                    report=json.loads((target/'report.json').read_text())
                    if report.get('profile')!='itmo_master' or report.get('totalRules')!=77:
                        raise RuntimeError('Unexpected profile/rule catalog in the actual report.')
                    item['counts']=report['counts'];item['reportHealth']=report['reportHealth']['status']
                manifest['jobs'].append(item)
                (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
            if pending:await asyncio.sleep(2)
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
    return 0 if all(j['status']=='completed' for j in manifest['jobs']) else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf',type=Path,action='append',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--model',default='google/gemini-2.5-flash')
    parser.add_argument('--allow-external-llm',action='store_true')
    parser.add_argument('--timeout',type=int,default=3600)
    args=parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__=='__main__':main()
