from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docx import Document
import pymupdf

from backend.app.checking.itmo_layout import (
    caption_format_issues, colored_spans, object_box, pagination, toc_rows,
)
from backend.app.checking.itmo_master import _centered, _inventory, run_itmo_rule
from backend.app.document.analyze import analyze_document
from backend.app.extraction import extract_document
from backend.app.rules.registry import rules_for_profile


RULES = {rule['id']: rule for rule in rules_for_profile('itmo_master')}


def row(text: str, y: float, *, kind: str = 'paragraph', x: float = 72) -> dict:
    box = [x, y, 500, y + 14]
    return {'text': text, 'type': kind, 'bbox': box, 'fontSize': 12,
            'pageWidth': 595, 'pageHeight': 842,
            'lines': [{'text': text, 'bbox': box, 'fontSize': 12}]}


def pdf_document(page_rows: list[list[dict]], links: dict | None = None) -> dict:
    pages = [{'number': number, 'width': 595, 'height': 842,
              'text': '\n'.join(r['text'] for r in rows), 'blocks': rows,
              'layoutObjects': {'links': (links or {}).get(number, [])}}
             for number, rows in enumerate(page_rows, 1)]
    return analyze_document('\n'.join(p['text'] for p in pages), pages, 'pdf')


def with_bibliography(tail: list[list[dict]], links: dict | None = None) -> dict:
    return pdf_document([[row('СОДЕРЖАНИЕ', 50, kind='heading')],
                         [row('ВВЕДЕНИЕ', 50, kind='heading'), row('Согласно работе [1], метод применим.', 100)],
                         *tail], links)


def link(uri: str, y: float) -> dict:
    return {'uri': uri, 'bbox': [180, y, 350, y + 12]}


class DocxLayoutTests(unittest.TestCase):
    def test_extracted_docx_caption_has_no_physical_page_and_does_not_crash(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'caption.docx'
            doc = Document()
            doc.add_heading('СОДЕРЖАНИЕ', level=1)
            doc.add_heading('ВВЕДЕНИЕ', level=1)
            doc.add_paragraph('На рисунке 1 приведена схема.')
            doc.add_paragraph('Рисунок 1 — Схема')
            doc.save(path)
            extracted = extract_document(path)
        caption = next(b for b in extracted['blocks'] if b['text'] == 'Рисунок 1 — Схема')
        self.assertNotIn('page', caption)
        self.assertNotIn('bbox', caption)
        inventory = _inventory(extracted)
        self.assertEqual(len(inventory['captions']), 1)
        for rule in RULES.values():
            if not str(rule.get('detectorId', '')).startswith('itmo:'):
                continue
            with self.subTest(rule=rule['id']):
                result = run_itmo_rule(rule, extracted)
                if not rule['detectorId'].startswith('itmo:presence:'):
                    self.assertEqual(result['status'], 'not_checked')
                    self.assertFalse(result['coverage']['exhaustive'])
                    self.assertEqual(result['evidence'], [])
        bad, observations = caption_format_issues(inventory['captions'], extracted, _centered)
        self.assertEqual(bad, [])
        self.assertEqual(observations[0]['alignment'], 'uncertain')
        self.assertEqual(observations[0]['position'], 'uncertain')
        self.assertIsNone(object_box(extracted, inventory['captions'][0]))
        self.assertFalse(pagination(extracted, inventory, _centered)['complete'])
        self.assertEqual(colored_spans(extracted, inventory), ([], [], 0))
        self.assertEqual(toc_rows(extracted, inventory), [])

    def test_missing_pdf_geometry_does_not_prove_caption_alignment(self) -> None:
        document = pdf_document([[{'text': 'Рисунок 1 — Схема', 'type': 'caption', 'fontSize': 12}]])
        inventory = _inventory(document)
        bad, observations = caption_format_issues(inventory['captions'], document, _centered)
        self.assertEqual(bad, [])
        self.assertEqual(observations[0]['alignment'], 'uncertain')
        self.assertEqual(observations[0]['position'], 'uncertain')

    def test_pdf_caption_checks_still_detect_real_layout_and_text_defects(self) -> None:
        document = pdf_document([[row('СОДЕРЖАНИЕ', 50, kind='heading')],
                                 [row('ВВЕДЕНИЕ', 50, kind='heading'),
                                  row('Рисунок 1 — Схема.', 300, kind='caption')]])
        result = run_itmo_rule(RULES['ITMO-4-5-3'], document)
        self.assertEqual(result['status'], 'violation')
        self.assertIn('caption_period', {e.get('check') for e in result['evidence']})
        self.assertTrue(all(e.get('page') == 2 and e.get('bbox') and e.get('blockId') for e in result['evidence']))

    def test_pdf_caption_continuations_retain_each_source_block(self) -> None:
        first = row('Рисунок 1 — Схема', 300, kind='caption', x=220)
        second = row('системы.', 316, x=220)
        for block in (first, second):
            block['bbox'][2] = 375
        document = pdf_document([[row('СОДЕРЖАНИЕ', 50, kind='heading')],
                                 [row('ВВЕДЕНИЕ', 50, kind='heading'), first, second]])
        caps = _inventory(document)['captions']
        self.assertEqual(len(caps), 1)
        self.assertEqual(len(caps[0]['blockIds']), 2)
        result = run_itmo_rule(RULES['ITMO-4-5-3'], document)
        period = next(e for e in result['evidence'] if e.get('check') == 'caption_period')
        self.assertEqual(period['blockId'], document['blocks'][-1]['id'])
        self.assertEqual(period['quote'], 'системы.')

    def test_pdf_object_assignment_and_caption_geometry_are_preserved(self) -> None:
        caption = row('Рисунок 1 — Схема', 300, kind='caption', x=220)
        caption['bbox'][2] = 375
        document = pdf_document([[row('СОДЕРЖАНИЕ', 50, kind='heading')],
                                 [row('ВВЕДЕНИЕ', 50, kind='heading'), caption]])
        image = [220, 200, 375, 290]
        document['pages'][1]['layoutObjects']['images'] = [image]
        caps = _inventory(document)['captions']
        self.assertEqual(object_box(document, caps[0]), image)
        bad, observations = caption_format_issues(caps, document, _centered)
        self.assertEqual(bad, [])
        self.assertEqual(observations[0]['alignment'], 'pass')
        self.assertEqual(observations[0]['position'], 'pass')
        self.assertEqual(run_itmo_rule(RULES['ITMO-4-5-3'], document)['status'], 'not_checked')


class TitlePageTests(unittest.TestCase):
    def title_document(self, titles: list[str], *, page: int = 1) -> dict:
        pages = [[] for _ in range(page - 1)]
        pages.append([*[row(title, 100 + i * 30, kind='title') for i, title in enumerate(titles)],
                      row('Обучающийся: Иванов И. И.', 400), row('Руководитель: Петров П. П.', 430)])
        return pdf_document(pages)

    def test_whole_multiline_and_split_title_have_real_evidence(self) -> None:
        for titles in (['ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА'],
                       ['ВЫПУСКНАЯ\nКВАЛИФИКАЦИОННАЯ\nРАБОТА'],
                       ['ВЫПУСКНАЯ', 'КВАЛИФИКАЦИОННАЯ', 'РАБОТА'],
                       ['ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ', 'РАБОТА']):
            with self.subTest(titles=titles):
                document = self.title_document(titles)
                result = run_itmo_rule(RULES['ITMO-3-2-titlepage'], document)
                self.assertEqual(result['status'], 'pass')
                self.assertEqual({e['blockId'] for e in result['evidence']},
                                 {b['id'] for b in document['blocks'][:len(titles)]})
                self.assertTrue(all(e.get('page') == 1 and e.get('bbox') and e['verified'] for e in result['evidence']))
                for evidence in result['evidence']:
                    parent = next(b for b in document['blocks'] if b['id'] == evidence['blockId'])
                    self.assertEqual(evidence['quote'], ' '.join(parent['text'].split()))

    def test_absent_partial_or_later_page_title_does_not_pass(self) -> None:
        for titles, page in (([], 1), (['ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ'], 1),
                             (['ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА'], 2)):
            with self.subTest(titles=titles, page=page):
                result = run_itmo_rule(RULES['ITMO-3-2-titlepage'], self.title_document(titles, page=page))
                self.assertEqual(result['status'], 'not_checked')
                self.assertEqual(result['evidence'], [])

    def test_title_without_required_requisites_does_not_pass(self) -> None:
        document = self.title_document(['ВЫПУСКНАЯ КВАЛИФИКАЦИОННАЯ РАБОТА'])
        document['blocks'] = document['blocks'][:1]
        self.assertEqual(run_itmo_rule(RULES['ITMO-3-2-titlepage'], document)['status'], 'not_checked')

    def test_real_pdf_split_english_title_preserves_extracted_sources(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'title.pdf'
            with pymupdf.open() as pdf:
                page = pdf.new_page()
                for y, text in ((80, "MASTER'S"), (130, 'THESIS'),
                                (200, 'Student: Test Author'), (230, 'Supervisor: Test Reviewer')):
                    page.insert_text((72, y), text)
                pdf.save(path)
            document = extract_document(path)
        result = run_itmo_rule(RULES['ITMO-3-2-titlepage'], document)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(len(result['evidence']), 2)
        self.assertEqual([e['quote'] for e in result['evidence']], ["MASTER'S", 'THESIS'])
        self.assertTrue(all(e.get('bbox') and e.get('page') == 1 for e in result['evidence']))


class BibliographyLinkTests(unittest.TestCase):
    def check(self, document: dict) -> dict:
        return run_itmo_rule(RULES['ITMO-A1-links'], document)

    def test_bibliography_url_is_reported_but_later_appendix_url_is_not(self) -> None:
        document = with_bibliography([
            [row('СПИСОК ИСТОЧНИКОВ', 50, kind='heading'), row('1. Ресурс https://example.org/bib', 100)],
            [row('ПРИЛОЖЕНИЕ А', 50, kind='heading'), row('https://example.org/appendix', 100)],
        ], {3: [link('https://example.org/bib', 100)], 4: [link('https://example.org/appendix', 100)]})
        result = self.check(document)
        self.assertEqual(result['status'], 'violation')
        self.assertEqual([e['uri'] for e in result['evidence']], ['https://example.org/bib'])
        self.assertEqual(result['evidence'][0]['blockId'], document['blocks'][4]['id'])
        self.assertEqual(result['evidence'][0]['sourceKind'], 'pdf_annotation')

    def test_same_page_boundaries_exclude_urls_before_and_after_bibliography(self) -> None:
        document = with_bibliography([[
            row('https://example.org/before', 25),
            row('БИБЛИОГРАФИЧЕСКИЙ СПИСОК', 60, kind='heading'),
            row('1. Ресурс https://example.org/bib', 100),
            row('Приложение А — Исходный код', 200, kind='heading'),
            row('https://example.org/appendix', 250),
        ]], {3: [link('https://example.org/before', 25), link('https://example.org/bib', 100),
                  link('https://example.org/appendix', 250)]})
        self.assertEqual([e['uri'] for e in self.check(document)['evidence']], ['https://example.org/bib'])

    def test_appendix_only_url_does_not_create_violation_or_pass(self) -> None:
        for heading in ('ПРИЛОЖЕНИЯ', 'ПРИЛОЖЕНИЕ А', 'APPENDIX A', 'APPENDICES', 'Appendix A: Source code'):
            with self.subTest(heading=heading):
                document = with_bibliography([
                    [row('REFERENCES', 50, kind='heading'), row('1. A printed reference.', 100)],
                    [row(heading, 50, kind='heading'), row('https://example.org/appendix', 100)],
                ], {4: [link('https://example.org/appendix', 100)]})
                result = self.check(document)
                self.assertIn(result['status'], {'uncertain', 'not_checked'})
                self.assertEqual(result['evidence'], [])

    def test_supported_bibliography_headings(self) -> None:
        for heading in ('Список использованных источников', 'Список литературы',
                        'Список использованной литературы', 'Библиографический список',
                        'ЛИТЕРАТУРА', 'REFERENCES', 'Bibliography', 'List of references',
                        'СПИСОК\nИСПОЛЬЗОВАННЫХ\nИСТОЧНИКОВ'):
            with self.subTest(heading=heading):
                document = with_bibliography([
                    [row(heading, 50, kind='heading'), row('1. Resource https://example.org/bib', 100)],
                ], {3: [link('https://example.org/bib', 100)]})
                self.assertEqual(self.check(document)['status'], 'violation')

    def test_unknown_end_does_not_make_arbitrary_later_url_a_reference(self) -> None:
        document = with_bibliography([[
            row('REFERENCES', 50, kind='heading'), row('1. A printed reference.', 100),
            row('https://example.org/unassigned', 300),
        ]], {3: [link('https://example.org/unassigned', 300)]})
        result = self.check(document)
        self.assertEqual(result['status'], 'uncertain')
        self.assertEqual(result['evidence'], [])

    def test_wrapped_url_in_numbered_source_block_is_checked_without_an_end_heading(self) -> None:
        entry = row('1. A reference\nhttps://example.org/bib', 100, kind='heading')
        entry['lines'] = [row('1. A reference', 100)['lines'][0],
                          row('https://example.org/bib', 120)['lines'][0]]
        document = with_bibliography([
            [row('REFERENCES', 50, kind='heading'), entry],
        ], {3: [link('https://example.org/bib', 120)]})
        self.assertEqual(self.check(document)['status'], 'violation')

    def test_missing_bibliography_scope_does_not_create_violation_or_pass(self) -> None:
        document = with_bibliography([
            [row('https://example.org/unassigned', 100)],
        ], {3: [link('https://example.org/unassigned', 100)]})
        self.assertEqual(self.check(document)['status'], 'not_checked')

    def test_confirmed_map_can_supply_bibliography_boundaries(self) -> None:
        document = with_bibliography([[
            row('Sources for this project', 50), row('1. A resource.', 100),
            row('https://example.org/bib', 130), row('https://example.org/appendix', 250),
        ]], {3: [link('https://example.org/bib', 130), link('https://example.org/appendix', 250)]})
        document['map'] = {'elements': [{'type': 'bibliography', 'state': 'confirmed',
                                        'startBlockId': document['blocks'][3]['id'],
                                        'endBlockId': document['blocks'][5]['id']}]}
        result = self.check(document)
        self.assertEqual([e['uri'] for e in result['evidence']], ['https://example.org/bib'])

    def test_missing_link_geometry_and_plain_url_text_are_not_violations(self) -> None:
        for annotation in ([], [{'uri': 'https://example.org/bib'}]):
            with self.subTest(annotation=annotation):
                document = with_bibliography([
                    [row('REFERENCES', 50, kind='heading'), row('1. Resource https://example.org/bib', 100)],
                ], {3: annotation})
                self.assertEqual(self.check(document)['status'], 'uncertain')

    def test_real_pdf_annotations_are_limited_to_bibliography_lines(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'links.pdf'
            with pymupdf.open() as pdf:
                for heading in ('CONTENTS', 'INTRODUCTION'):
                    page = pdf.new_page()
                    page.insert_text((72, 80), heading)
                page = pdf.new_page()
                for y, text in ((80, 'REFERENCES'), (130, '1. Resource https://example.org/bib'),
                                (220, 'APPENDIX A'), (270, 'https://example.org/appendix')):
                    page.insert_text((72, y), text)
                for y, uri in ((130, 'https://example.org/bib'), (270, 'https://example.org/appendix')):
                    page.insert_link({'kind': pymupdf.LINK_URI, 'from': pymupdf.Rect(72, y - 12, 300, y + 2), 'uri': uri})
                pdf.save(path)
            document = extract_document(path, layout_objects=True)
        result = self.check(document)
        self.assertEqual(result['status'], 'violation')
        self.assertEqual([e['uri'] for e in result['evidence']], ['https://example.org/bib'])
        self.assertTrue(result['evidence'][0].get('blockId'))
        self.assertTrue(result['evidence'][0].get('bbox'))


class CitationVerdictTests(unittest.TestCase):
    def test_interval_does_not_create_a_dangling_citation_violation(self) -> None:
        for text, expected in (('x из отрезка [10; 20]. Согласно работе [1], метод применим.', 'uncertain'),
                               ('Согласно работам [10, 20], метод применим.', 'violation'),
                               ('Согласно работе [10], метод применим.', 'violation'),
                               ('Согласно работам [10–12], метод применим.', 'violation')):
            with self.subTest(text=text):
                document = with_bibliography([
                    [row('СПИСОК ИСТОЧНИКОВ', 50, kind='heading'), row('1. Источник. 2026.', 100)],
                ])
                document['blocks'][2]['text'] = text
                document['blocks'][2]['lines'][0]['text'] = text
                result = run_itmo_rule(RULES['ITMO-4-10-1'], document)
                self.assertEqual(result['status'], expected)
                if expected == 'violation':
                    self.assertTrue(all(e.get('blockId') == document['blocks'][2]['id'] for e in result['evidence']))


if __name__ == '__main__':
    unittest.main()
