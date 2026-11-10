from __future__ import annotations

import unittest

from backend.app.checking.bibliography import numeric_citations


class NumericCitationTests(unittest.TestCase):
    def test_real_citation_forms_and_source_offsets(self) -> None:
        for citation, numbers in (
            ('[10]', [10]), ('[10, 20]', [10, 20]),
            ('[10–12]', [10, 11, 12]), ('[10-12, 20]', [10, 11, 12, 20]),
            ('[10, 11–13; 20]', [10, 11, 12, 13, 20]),
            ('[1—3]', [1, 2, 3]), ('[1−3]', [1, 2, 3]),
        ):
            with self.subTest(citation=citation):
                text = f'Согласно исследованию {citation}, метод применим.'
                rows = numeric_citations(text)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['numbers'], numbers)
                self.assertEqual(text[rows[0]['start']:rows[0]['end']], citation)

    def test_mathematical_intervals_are_not_citations(self) -> None:
        for text in (
            'x из отрезка [10; 20]', 'x на отрезке [10, 20]',
            'Число из интервала допустимых значений [10; 20].',
            'Диапазон значений [10, 20].', 'Промежуток [10, 20].',
            'Числовые границы: [10, 20].', 'x ∈ [10, 20]',
            r'x \in [10, 20]', 'x принадлежит [10, 20]',
            'The closed interval [10, 20].', 'The range [10–20].',
            'The segment [10, 20].', 'The bounds are [10, 20].',
            'Выбран [10; 20].', 'x из отрезка [-10; 20]',
            'Интервал [1.5; 2.5]', 'Значение [0,1; 0,9]',
        ):
            with self.subTest(text=text):
                self.assertEqual(numeric_citations(text), [])

    def test_explicit_citation_context_preserves_semicolon_lists(self) -> None:
        for prefix in ('см. ', 'источники: ', 'работы ', 'See ', 'References: '):
            with self.subTest(prefix=prefix):
                self.assertEqual(numeric_citations(prefix + '[10; 20]')[0]['numbers'], [10, 20])

    def test_math_expression_does_not_hide_later_real_citations(self) -> None:
        text = 'Отрезок [10; 20] исследован в работах [10, 20] и [21–23].'
        self.assertEqual([row['numbers'] for row in numeric_citations(text)], [[10, 20], [21, 22, 23]])
        self.assertEqual(numeric_citations('Длина интервала обсуждается в источнике [10].')[0]['numbers'], [10])

    def test_mathematical_topic_alone_does_not_hide_a_citation(self) -> None:
        for text in ('Метод анализа интервалов описан в [10, 20].',
                     'Длина отрезка исследована в [10, 20].',
                     'A method for intervals is described in [10, 20].',
                     'Оценка диапазона применяется согласно [10–12].'):
            with self.subTest(text=text):
                self.assertEqual(len(numeric_citations(text)), 1)

    def test_invalid_numbers_and_unrelated_brackets_are_not_citations(self) -> None:
        for text in ('[0]', '[20–10]', '[1–999]', '[2026]', '[Автор, 2026]', '[a; b]'):
            with self.subTest(text=text):
                self.assertEqual(numeric_citations(text), [])


if __name__ == '__main__':
    unittest.main()
