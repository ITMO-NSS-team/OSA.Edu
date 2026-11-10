from __future__ import annotations

import unittest

from backend.app.routing.applicability import (
    _degree,
    evaluate_applicability,
    scientific_dissertation,
    technical_dissertation,
)
from backend.app.routing.rule_router import build_routing


def document(*texts: str, page: int | None = 1) -> dict:
    return {'blocks': [{'id': f'b{index}', 'text': text, 'page': page}
                       for index, text in enumerate(texts)]}


class WorkDegreeTests(unittest.TestCase):
    def test_master_vkr_with_scientific_supervisor(self) -> None:
        for degree in ('кандидат технических наук', 'доктор технических наук'):
            for title in ('Выпускная квалификационная работа\nКвалификация: магистр',
                          'Магистерская ВКР', 'Магистерская диссертация'):
                with self.subTest(degree=degree, title=title):
                    doc = document(title, f'Научный руководитель: {degree}, Иванов И. И.')
                    self.assertEqual(_degree(doc)[0], 'master')
                    self.assertIs(scientific_dissertation(doc)[0], False)
                    self.assertIs(technical_dissertation(doc)[0], False)

    def test_generic_vkr_does_not_inherit_supervisor_degree(self) -> None:
        for degree in ('кандидат', 'доктор'):
            with self.subTest(degree=degree):
                doc = document('Выпускная квалификационная работа',
                               'Научный руководитель', f'{degree} технических наук')
                self.assertEqual(_degree(doc)[0], 'vkr_unknown')
                self.assertIs(scientific_dissertation(doc)[0], False)

    def test_personnel_degrees_are_not_work_evidence(self) -> None:
        for role in ('Научный руководитель', 'Научный консультант', 'Рецензент',
                     'Официальные оппоненты', 'Заведующий кафедрой'):
            for page in (1, None):
                with self.subTest(role=role, page=page):
                    doc = document('Диссертация', role, 'кандидат технических наук',
                                   'доктор физико-математических наук', page=page)
                    self.assertIsNone(_degree(doc)[0])
                    self.assertIsNone(scientific_dissertation(doc)[0])
                    self.assertIsNone(technical_dissertation(doc)[0])

    def test_personnel_qualifications_do_not_set_student_level(self) -> None:
        doc = document('Выпускная квалификационная работа',
                       'Консультант: квалификация магистр, кандидат технических наук')
        self.assertEqual(_degree(doc)[0], 'vkr_unknown')

    def test_work_title_after_approval_header_is_preserved(self) -> None:
        for blocks in (
            ('УТВЕРЖДАЮ\nЗаведующий кафедрой\nдоктор технических наук\n'
             'Выпускная квалификационная работа\nКвалификация: магистр',),
            ('Заведующий кафедрой', 'доктор технических наук',
             'Магистерская диссертация'),
        ):
            with self.subTest(blocks=blocks):
                self.assertEqual(_degree(document(*blocks))[0], 'master')

    def test_personnel_sections_do_not_hide_work_titles_on_next_page(self) -> None:
        doc = document('Научный руководитель: кандидат технических наук')
        doc['blocks'].append({'id': 'b1', 'page': 2,
                              'text': 'Диссертация на соискание степени доктора технических наук'})
        self.assertEqual(_degree(doc)[0], 'doctor')
        self.assertIs(technical_dissertation(doc)[0], True)

    def test_bare_person_degrees_do_not_prove_a_dissertation(self) -> None:
        for text in ('кандидат технических наук Иванов', 'доктор технических наук Петров'):
            with self.subTest(text=text):
                self.assertIsNone(_degree(document(text))[0])

    def test_explicit_work_titles_and_qualifications(self) -> None:
        for title, expected in (
            ('Бакалаврская работа', 'bachelor'),
            ('Бакалаврская выпускная квалификационная работа', 'bachelor'),
            ('Квалификация: бакалавр', 'bachelor'),
            ('Bachelor’s thesis', 'bachelor'),
            ('Уровень высшего образования: магистратура', 'master'),
            ("Master's thesis", 'master'),
            ('Дипломная работа специалиста', 'specialist'),
            ('Кандидатская диссертация', 'candidate'),
            ('Докторская диссертация', 'doctor'),
        ):
            with self.subTest(title=title):
                self.assertEqual(_degree(document(title))[0], expected)

    def test_awarded_degree_and_branch_belong_to_the_work(self) -> None:
        for degree, expected in (('кандидата', 'candidate'), ('доктора', 'doctor')):
            for branch, technical in (('технических', True), ('физико-математических', False)):
                with self.subTest(degree=degree, branch=branch):
                    doc = document('ДИССЕРТАЦИЯ', 'на соискание учёной степени',
                                   f'{degree} {branch} наук',
                                   'Научный руководитель: кандидат технических наук',
                                   'Научный консультант: доктор экономических наук')
                    self.assertEqual(_degree(doc)[0], expected)
                    self.assertIs(scientific_dissertation(doc)[0], True)
                    self.assertIs(technical_dissertation(doc)[0], technical)

    def test_branch_cannot_be_borrowed_from_supervisor(self) -> None:
        for title in ('Докторская диссертация',
                      'Диссертация на соискание ученой степени кандидата'):
            with self.subTest(title=title):
                doc = document(title, 'Научный руководитель: доктор технических наук')
                self.assertIs(scientific_dissertation(doc)[0], True)
                self.assertIsNone(technical_dissertation(doc)[0])

    def test_confirmed_context_has_priority_but_does_not_prove_branch(self) -> None:
        doc = document('Научный руководитель: доктор технических наук')
        for level in ('bachelor', 'master', 'candidate', 'doctor'):
            with self.subTest(level=level):
                doc['workContext'] = {'confirmed': True, 'educationLevel': level}
                self.assertEqual(_degree(doc)[0], level)
                self.assertIs(technical_dissertation(doc)[0],
                              None if level in {'candidate', 'doctor'} else False)
        doc['workContext'] = {'confirmed': False, 'educationLevel': 'candidate'}
        self.assertIsNone(_degree(doc)[0])

    def test_conflicting_work_titles_stay_unknown(self) -> None:
        for titles in (('Магистерская диссертация', 'Кандидатская диссертация'),
                       ('Диссертация на соискание степени кандидата технических наук',
                        'Диссертация на соискание степени доктора технических наук')):
            with self.subTest(titles=titles):
                doc = document(*titles)
                self.assertIsNone(_degree(doc)[0])
                self.assertIsNone(scientific_dissertation(doc)[0])

    def test_conflicting_scientific_branches_stay_unknown(self) -> None:
        doc = document('Диссертация на соискание степени кандидата технических наук',
                       'Диссертация на соискание степени кандидата экономических наук')
        self.assertIsNone(technical_dissertation(doc)[0])

    def test_later_body_and_reference_mentions_are_not_title_evidence(self) -> None:
        self.assertIsNone(_degree(document('Кандидатская диссертация', page=20))[0])
        self.assertIsNone(_degree(document(*(['Текст'] * 50), 'Кандидатская диссертация', page=None))[0])

    def test_no_predicate_keeps_existing_applicability(self) -> None:
        self.assertEqual(evaluate_applicability({}, document('Магистерская ВКР')), (None, None))


class ApplicabilityRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_work_evidence_controls_routing_without_affecting_other_rules(self) -> None:
        for title, strategy in (('Магистерская ВКР', 'not_applicable'),
                                ('Диссертация', 'applicability_unknown'),
                                ('Кандидатская диссертация', 'structural')):
            with self.subTest(title=title):
                rules = [
                    {'id': 'TEST-SCIENTIFIC', 'routing': {'strategy': 'structural'},
                     'applicability': {'predicate': 'scientific_dissertation'}},
                    {'id': 'TEST-OTHER', 'routing': {'strategy': 'deterministic'}},
                ]
                result = await build_routing(
                    document=document(title, 'Руководитель: кандидат технических наук'),
                    map_value={'elements': []}, rules=rules,
                )
                self.assertEqual([row['strategy'] for row in result['routed']],
                                 [strategy, 'deterministic'])


if __name__ == '__main__':
    unittest.main()
