#!/usr/bin/env python3
"""Batch upload, inspect and download OSA.Edu SlideGuard jobs. No direct LLM calls.

Uses httpx from OSA.Edu's existing requirements. A manifest is saved after each
accepted batch so interrupted clients can resume polling without resubmission.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import sys
import time
from pathlib import Path

import httpx

TERMINAL = {'completed', 'partial', 'failed', 'cancelled'}


def checked(response):
    if response.is_error:
        try:
            body = response.json()
            message = body.get('error') or body.get('detail') or response.text
        except ValueError:
            message = response.text
        raise RuntimeError(f'HTTP {response.status_code}: {message}')
    return response.json()


def save(path, manifest):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def upload(args, client):
    if args.manifest.exists():
        raise ValueError('Manifest already exists. Use status to resume polling or select a new --manifest.')
    paths = []
    for raw in args.paths:
        path = Path(raw)
        if path.is_dir():
            paths.extend(p for p in sorted(path.rglob('*')) if p.is_file() and p.suffix.lower() == '.pdf')
        elif path.is_file() and path.suffix.lower() == '.pdf':
            paths.append(path)
        else:
            raise ValueError(f'Not a PDF file or directory: {path}')
    paths = list(dict.fromkeys(p.resolve() for p in paths))
    if not paths:
        raise ValueError('No PDFs found.')
    runtime = checked(client.get('/api/presentations/status', timeout=60))
    if not runtime['ok']:
        raise RuntimeError(runtime.get('error') or 'SlideGuard or its LLM configuration is unavailable.')
    size = min(args.batch_size, runtime['maxBatch'])
    manifest = {'api': str(client.base_url).rstrip('/'), 'jobs': []}
    for start in range(0, len(paths), size):
        with ExitStack() as stack:
            files = [('files', (p.name, stack.enter_context(p.open('rb')), 'application/pdf')) for p in paths[start:start+size]]
            jobs = checked(client.post('/api/presentations/jobs', files=files,
                data={'presentationType': args.presentation_type, 'language': args.language,
                      'criteria': json.dumps(args.criteria)}, timeout=180))
        manifest['jobs'].extend({'id': job['id'], 'originalName': job['originalName']} for job in jobs)
        save(args.manifest, manifest)
        print(f'Accepted {len(manifest["jobs"])}/{len(paths)}; manifest: {args.manifest}', flush=True)
    return manifest


def collect(client, identifiers):
    pending = set(identifiers)
    found = {}
    offset = 0
    while pending:
        page = checked(client.get('/api/presentations/jobs', params={'limit': 200, 'offset': offset}))
        for job in page:
            if job['id'] in pending:
                found[job['id']] = job
                pending.remove(job['id'])
        if len(page) < 200:
            break
        offset += 200
    if pending:
        raise RuntimeError(f'Jobs not found: {", ".join(sorted(pending))}')
    return found


def observe(args, client, manifest):
    deadline = time.monotonic() + args.timeout
    while True:
        found = collect(client, [job['id'] for job in manifest['jobs']])
        counts = {}
        for item in found.values():
            counts[item['status']] = counts.get(item['status'], 0) + 1
        print(json.dumps(counts, ensure_ascii=False), flush=True)
        manifest['statuses'] = {key: item['status'] for key, item in found.items()}
        save(args.manifest, manifest)
        done = all(job['status'] in TERMINAL for job in found.values())
        if done or not args.wait:
            if args.download_dir:
                args.download_dir.mkdir(parents=True, exist_ok=True)
                for identifier, job in found.items():
                    for suffix, available in [('json', job['resultAvailable']), ('pdf', job['reportAvailable'])]:
                        if not available:
                            continue
                        response = client.get(f'/api/presentations/jobs/{identifier}/report.{suffix}')
                        response.raise_for_status()
                        target = args.download_dir / f'{identifier}.{suffix}'
                        target.write_bytes(response.content)
            return 0 if not done or all(j['status'] == 'completed' for j in found.values()) else 2
        if time.monotonic() >= deadline:
            raise TimeoutError('Polling timeout. Resume with the status command and the saved manifest.')
        time.sleep(args.poll_interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['upload', 'status'])
    parser.add_argument('paths', nargs='*')
    parser.add_argument('--url', default=None, help='API base URL; status defaults to the manifest URL')
    parser.add_argument('--manifest', type=Path, default=Path('data/presentations/batch.json'))
    parser.add_argument('--presentation-type', choices=['scientific', 'industrial', 'collaborative', 'technological'], default='scientific')
    parser.add_argument('--language', choices=['ru', 'en'], default='ru')
    parser.add_argument('--criteria', action='append', default=[])
    parser.add_argument('--batch-size', type=int, default=10)
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--download-dir', type=Path)
    parser.add_argument('--timeout', type=int, default=86400)
    parser.add_argument('--poll-interval', type=float, default=5)
    args = parser.parse_args()
    if args.batch_size < 1 or args.poll_interval <= 0:
        parser.error('--batch-size and --poll-interval must be positive')
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8')) if args.command == 'status' else None
        base = args.url or (manifest['api'] if manifest else 'http://127.0.0.1:8787')
        with httpx.Client(base_url=base.rstrip('/'), timeout=60) as client:
            if args.command == 'upload':
                manifest = upload(args, client)
            return observe(args, client, manifest)
    except (OSError, ValueError, RuntimeError, TimeoutError, httpx.HTTPError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
