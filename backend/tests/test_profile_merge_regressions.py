"""Offline cross-profile regressions for the ITMO/VkrCheck/Light Parser merge."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import AsyncMock, patch

from docx import Document
import httpx
import pymupdf

from backend.app import main
from backend.app.document.map_builder import refresh_map
from backend.app.extraction import extract_document
from backend.app.orchestration.checker import check_document
from backend.app.pdf_reporting import report_to_pdf
from backend.app.reporting import make_report
from backend.app.reproducibility.thesis_parser import document_sections, parse_thesis_pdf, TextLayerRequiredError
from backend.app.rules.registry import PROFILE_LABELS, load_rule_registry, map_prompt_for_profile, profile_label, rules_for_profile
from backend.app.user_pdf_reporting import report_to_user_pdf
from backend.app.util import empty_usage


PROFILES = ('core', 'full', 'vkrcheck', 'itmo_master')
HTTPX_SEND = httpx.AsyncClient.send


def report_fixture(profile):
    rules = rules_for_profile(profile)
    rule = next((r for r in rules if r['id'] == 'ITMO-4-2-a4'), rules[0])
    evidence = [{'page': p, 'blockId': f'fixture-{p}', 'quote': 'Page size: 200 x 300 mm',
        'reason': 'Fixture geometry observation', 'verified': True, 'bbox': [10, 10, 30, 30]}
        for p in range(1, 8)]
    result = {'ruleId': rule['id'], 'status': 'violation', 'severity': rule['severity'],
        'explanation': 'Fixture violation with exact source evidence.', 'fix': 'Correct the fixture.',
        'confidence': 1, 'checkedBy': 'deterministic', 'evidenceStatus': 'verified', 'evidence': evidence}
    return make_report([rule], [result], [], empty_usage(), None,
        {'strategy': 'manifest', 'fragments': 0, 'checkRequests': 0, 'explicitRules': 1, 'fallbackRules': 0},
        {'profile': profile, 'model': 'offline-fixture', 'provider': 'openrouter'})


class ProfileMergeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Fail closed: ASGI requests are in-process; no host CLI or remote API.
        for guard in [patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')),
                      patch.object(subprocess, 'run', side_effect=AssertionError('subprocess forbidden')),
                      patch('backend.app.orchestration.checker.ask_structured_json',
                            new=AsyncMock(side_effect=AssertionError('LLM forbidden')))]:
            guard.start(); self.addCleanup(guard.stop)
        async def local_only(client, request, **kwargs):
            if not isinstance(client._transport, httpx.ASGITransport):
                raise AssertionError('External HTTP forbidden')
            return await HTTPX_SEND(client, request, **kwargs)
        guard = patch.object(httpx.AsyncClient, 'send', local_only)
        guard.start(); self.addCleanup(guard.stop)

    def test_registries_are_complete_disjoint_and_keep_existing_additional_rule_policy(self):
        registry = load_rule_registry()
        self.assertEqual(set(PROFILES), set(PROFILE_LABELS))
        expected_counts = {'core': 65, 'full': 230, 'vkrcheck': 6, 'itmo_master': 77}
        for profile in PROFILES:
            with self.subTest(profile=profile):
                rules = rules_for_profile(profile)
                self.assertEqual(expected_counts[profile], len(rules))
                self.assertEqual(len(rules), len({r['id'] for r in rules}))
                self.assertNotEqual('—', profile_label(profile))
                extra = rules_for_profile(profile, 'Необходимо описать ограничения исследования.')
                self.assertEqual(len(rules) + (profile != 'vkrcheck'), len(extra))
        self.assertEqual(registry['core'] + registry['soft'], rules_for_profile('full'))
        vkr = {r['id'] for r in rules_for_profile('vkrcheck')}
        itmo = {r['id'] for r in rules_for_profile('itmo_master')}
        self.assertFalse(vkr & itmo)
        self.assertFalse((vkr | itmo) & {r['id'] for r in rules_for_profile('full')})
        with self.assertRaises(ValueError): rules_for_profile('unknown')

    async def test_rules_and_health_api_expose_all_profiles_and_itmo_source_anchors(self):
        with patch.object(main, 'host_provider_status', return_value={'authenticated': False, 'detail': 'offline'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://local') as client:
                health = await client.get('/api/health')
                self.assertEqual(200, health.status_code)
                self.assertEqual(77, health.json()['knowledge']['itmoMasterCount'])
                for profile in PROFILES:
                    response = await client.get('/api/rules', params={'profile': profile})
                    self.assertEqual(200, response.status_code)
                    self.assertEqual([r['id'] for r in rules_for_profile(profile)], [r['id'] for r in response.json()])
                    if profile == 'itmo_master':
                        self.assertTrue(all(r['sourceUrl'].startswith('/api/rule-sources/itmo_master.pdf#page=') for r in response.json()))
                self.assertEqual(400, (await client.get('/api/rules?profile=unknown')).status_code)

    async def test_upload_preserves_selected_profile_and_adds_itmo_guidance_only_for_itmo(self):
        selected = next(m['id'] for m in main.MODELS if m['provider'] == 'openrouter')
        persist, start = AsyncMock(), unittest.mock.Mock()
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'offline-not-a-real-key'}), \
             patch.object(main, 'UPLOADS_DIR', self.root), patch.object(main, 'create_jobs', persist), \
             patch.object(main, 'start_queue', start):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://local') as client:
                for profile in PROFILES:
                    response = await client.post('/api/jobs', files={'files': ('fixture.pdf', b'%PDF-fixture', 'application/pdf')},
                        data={'profile': profile, 'model': selected})
                    self.assertEqual(201, response.status_code, response.text)
                    job = response.json()[0]
                    self.assertEqual(profile, job['profile'])
                    self.assertEqual(map_prompt_for_profile(main.DEFAULT_MAP_PROMPT, profile), job['mapPrompt'])
                    self.assertEqual(job, persist.call_args.args[0][0])
                invalid = await client.post('/api/jobs', files={'files': ('fixture.pdf', b'%PDF-fixture')},
                    data={'profile': 'unknown', 'model': selected})
                self.assertEqual(400, invalid.status_code)
        self.assertEqual(4, persist.await_count)
        self.assertEqual(4, start.call_count)
        prompt = map_prompt_for_profile(main.DEFAULT_MAP_PROMPT, 'itmo_master')
        self.assertEqual(prompt, map_prompt_for_profile(prompt, 'itmo_master'))

    async def test_normative_pdf_endpoint_serves_only_configured_pdf_and_handles_missing_file(self):
        path = self.root / 'normative.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 60), 'ITMO normative fixture')
            pdf.save(path)
        with patch.dict(os.environ, {'ITMO_REQUIREMENTS_PDF': str(path)}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://local') as client:
                response = await client.get('/api/rule-sources/itmo_master.pdf')
                self.assertEqual(200, response.status_code)
                self.assertEqual('application/pdf', response.headers['content-type'])
                self.assertEqual('inline', response.headers['content-disposition'])
                self.assertEqual(path.read_bytes(), response.content)
                self.assertEqual(404, (await client.get('/api/rule-sources/student.pdf')).status_code)
                path.unlink()
                self.assertEqual(404, (await client.get('/api/rule-sources/itmo_master.pdf')).status_code)

    async def test_offline_checker_keeps_both_engines_and_complete_catalogs(self):
        doc = Document()
        for kind, title, text in [
            ('introduction', 'Введение', 'Метод исследования и цель работы. ' * 25),
            ('chapter', '1 Первая глава', 'Исследование первого метода.'),
            ('chapter', '2 Вторая глава', 'Исследование второго метода.'),
            ('conclusion', 'Заключение', 'Результаты исследования и выводы.'),
            ('bibliography', 'Список использованных источников', None),
        ]:
            doc.add_heading(title, 1)
            if text: doc.add_paragraph(text)
            else:
                for n in range(1, 6): doc.add_paragraph(f'{n}. Author A. Research paper. Publisher, 2020.')
        path = self.root / 'thesis.docx'; doc.save(path)
        document = extract_document(path)
        blocks = document['blocks']
        # The fixture's user-confirmed map follows actual DOCX heading styles;
        # numbered bibliography rows can also be heuristic heading candidates.
        starts = [i for i, b in enumerate(blocks) if str(b.get('style', '')).startswith('Heading')]
        kinds = ['introduction', 'chapter', 'chapter', 'conclusion', 'bibliography']
        elements = [{'id': f'section-{n}', 'type': kind, 'label': blocks[start]['text'], 'state': 'confirmed',
            'startBlockId': blocks[start]['id'], 'endBlockId': blocks[end - 1]['id'],
            'blockIds': [b['id'] for b in blocks[start:end]], 'confidence': 1, 'source': 'user'}
            for n, (kind, start, end) in enumerate(zip(kinds, starts, starts[1:] + [len(blocks)]))]
        document['map'] = refresh_map(document, {'elements': elements, 'relations': [], 'review': {'confirmedByUser': True}})
        for profile in PROFILES:
            checked = await check_document(document=copy.deepcopy(document), provider='openrouter', model='offline-fixture',
                prompt='offline fixture', profile=profile, additional_criteria='', allow_external_llm=False)
            self.assertEqual({r['id'] for r in rules_for_profile(profile)}, {r['ruleId'] for r in checked['results']})
            self.assertEqual(0, checked['llmUsage']['requests'])
            if profile == 'vkrcheck':
                statuses = {r['ruleId']: r['status'] for r in checked['results']}
                for key in ('VKR-SECTIONS', 'VKR-CHAPTERS', 'VKR-INTRO-LENGTH', 'VKR-REFERENCES'):
                    self.assertEqual('pass', statuses[key], next(r['explanation'] for r in checked['results'] if r['ruleId'] == key))
                self.assertEqual('not_checked', statuses['VKR-INTRO-CONTENT'])
            if profile == 'itmo_master':
                self.assertTrue(any(r.get('normativeReference') for r in checked['results']))

    def test_user_and_developer_pdfs_keep_four_profile_labels_evidence_and_itmo_coverage(self):
        for profile in PROFILES:
            report = report_fixture(profile)
            original = copy.deepcopy(report)
            self.assertEqual(PROFILE_LABELS[profile], report['profileLabel'])
            self.assertEqual(profile == 'itmo_master', 'coverageMetrics' in report)
            for renderer in (report_to_pdf, report_to_user_pdf):
                with self.subTest(profile=profile, renderer=renderer.__name__):
                    raw = renderer('fixture.pdf', report)
                    self.assertTrue(raw.startswith(b'%PDF-'))
                    with pymupdf.open(stream=raw, filetype='pdf') as pdf:
                        text = ' '.join(' '.join(page.get_text().split()) for page in pdf)
                    self.assertIn(PROFILE_LABELS[profile], text)
                    self.assertIn('Page size: 200 x 300', text)
                    if renderer is report_to_pdf:
                        self.assertIn('Page size: 200 x 300 mm', text)
                    if profile == 'itmo_master':
                        self.assertIn('Требования к ВКР ИТМО', text)
            self.assertEqual(original, report, 'Presentation must not discard original evidence in report JSON.')
            self.assertEqual(7, len(report['ruleResults'][0]['evidence']))

    def test_light_parser_still_reads_text_layer_and_rejects_empty_scan(self):
        path = self.root / 'paper.pdf'
        text = 'The experiment compares a baseline and the proposed method on the same dataset. ' * 9
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 45), 'INTRODUCTION', fontsize=18)
            page.insert_textbox(pymupdf.Rect(40, 80, 550, 740), text, fontsize=11)
            pdf.save(path)
        sections, document = parse_thesis_pdf(path)
        self.assertTrue(sections)
        self.assertIn('The experiment', ' '.join(s['text'] for s in sections))
        self.assertEqual('pdf', document['sourceFormat'])
        self.assertEqual(sections, document_sections(document))
        blank = self.root / 'scan.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page(); pdf.save(blank)
        with self.assertRaises(TextLayerRequiredError): parse_thesis_pdf(blank)
