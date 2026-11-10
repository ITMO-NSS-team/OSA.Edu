"""CLI contract tests with temporary synthetic data, never a quality benchmark."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from backend.app.language.evaluation import evaluate


ROOT = Path(__file__).resolve().parents[2]


class LanguageEvaluationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.gold_path = self.directory / 'synthetic-gold.json'
        self.predictions_path = self.directory / 'synthetic-predictions.json'
        # These invented sentences only exercise scoring and CLI plumbing. They
        # are not shipped as gold annotations or used to report model quality.
        self.gold = {'cases': [
            {'id': 'synthetic-spelling', 'source': 'unit_test_only', 'page': 1,
             'text': 'В тексте ашибка.', 'expected': [
                 {'id': 'synthetic-error', 'category': 'spelling', 'quote': 'ашибка',
                  'allowedReplacements': ['ошибка']},
             ]},
            {'id': 'synthetic-punctuation', 'source': 'unit_test_only', 'page': 2,
             'text': 'В отличие от метода система работает быстрее.', 'expected': []},
            {'id': 'excluded-regression', 'source': 'authored_regression', 'page': 3,
             'text': 'Тестовая ашибка.', 'expected': [
                 {'id': 'excluded-error', 'category': 'spelling', 'quote': 'ашибка',
                  'allowedReplacements': ['ошибка']},
             ]},
        ]}
        self.predictions = {'issues': [
            {'id': 'synthetic-prediction', 'page': 1, 'quote': 'ашибка',
             'replacement': 'ошибка', 'category': 'spelling',
             'explanation': 'Тестовая орфографическая правка.'},
            {'id': 'synthetic-false-positive', 'page': 2,
             'quote': 'В отличие от метода', 'replacement': 'В отличие от метода,',
             'category': 'punctuation', 'explanation': 'Тестовая спорная пунктуационная правка.'},
        ], 'checkedChunks': 2, 'totalChunks': 3, 'complete': False,
            'usage': {'requests': 7}}
        self.write_inputs()

    def write_inputs(self) -> None:
        self.gold_path.write_text(json.dumps(self.gold, ensure_ascii=False), encoding='utf-8')
        self.predictions_path.write_text(json.dumps(self.predictions, ensure_ascii=False), encoding='utf-8')

    def run_cli(self, *arguments: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, '-m', 'scripts.evaluate_language_review', *arguments],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )

    def inputs(self) -> list[str]:
        return ['--gold', str(self.gold_path), '--predictions', str(self.predictions_path)]

    def assert_cli_error(self, result: subprocess.CompletedProcess, *details: str) -> None:
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertNotIn('Traceback', result.stderr)
        for detail in details:
            self.assertIn(detail, result.stderr)

    def test_saved_predictions_score_and_write_reproducible_output(self) -> None:
        output = self.directory / 'metrics.json'
        first = self.run_cli(*self.inputs(), '--output', str(output))
        self.assertEqual(first.returncode, 0, first.stderr)
        scored = json.loads(first.stdout)
        self.assertEqual(scored, json.loads(output.read_text(encoding='utf-8')))
        self.assertEqual(scored['cases'], 2)
        self.assertEqual((scored['overall']['tp'], scored['overall']['fp'], scored['overall']['fn']), (1, 1, 0))
        self.assertEqual(scored['overall']['precision'], 0.5)
        self.assertEqual(scored['overall']['recall'], 1)
        self.assertEqual(scored['mode'], 'saved_baseline')
        self.assertEqual(scored['paidRequestsMade'], 0)
        self.assertEqual(scored['baselineCoverage'], {'checkedChunks': 2, 'totalChunks': 3, 'complete': False})
        self.assertEqual(scored['baselineUsage'], {'requests': 7})
        second = self.run_cli(*self.inputs())
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(first.stdout, second.stdout)

    def test_guards_remove_disputed_punctuation_but_keep_valid_spelling(self) -> None:
        result = self.run_cli(*self.inputs(), '--guards')
        self.assertEqual(result.returncode, 0, result.stderr)
        scored = json.loads(result.stdout)
        self.assertEqual((scored['overall']['tp'], scored['overall']['fp']), (1, 0))
        self.assertEqual(scored['mode'], 'saved_candidates_programmatic_guards_only')
        self.assertEqual(scored['paidRequestsMade'], 0)

    def test_job_export_wrapper_and_preprojected_predictions(self) -> None:
        for row, case in zip(self.predictions['issues'], self.gold['cases']):
            row['caseId'] = case['id']
        self.predictions = {'result': self.predictions}
        self.write_inputs()
        result = self.run_cli(*self.inputs(), '--guards')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['overall']['tp'], 1)

    def test_both_paths_are_required(self) -> None:
        self.assert_cli_error(self.run_cli(), '--gold', '--predictions', 'required')
        self.assert_cli_error(self.run_cli('--gold', str(self.gold_path)), '--predictions', 'required')
        self.assert_cli_error(self.run_cli('--predictions', str(self.predictions_path)), '--gold', 'required')

    def test_missing_gold_file(self) -> None:
        self.gold_path.unlink()
        self.assert_cli_error(self.run_cli(*self.inputs()), '--gold', str(self.gold_path), 'cannot read JSON')

    def test_missing_predictions_file(self) -> None:
        self.predictions_path.unlink()
        self.assert_cli_error(self.run_cli(*self.inputs()), '--predictions', str(self.predictions_path), 'cannot read JSON')

    def test_directory_instead_of_file(self) -> None:
        result = self.run_cli('--gold', str(self.directory), '--predictions', str(self.predictions_path))
        self.assert_cli_error(result, '--gold', str(self.directory), 'cannot read JSON')

    def test_invalid_json_and_encoding(self) -> None:
        for path, option in ((self.gold_path, '--gold'), (self.predictions_path, '--predictions')):
            for content in (b'{broken', b'\xff'):
                with self.subTest(option=option, content=content):
                    self.write_inputs()
                    path.write_bytes(content)
                    self.assert_cli_error(self.run_cli(*self.inputs()), option, str(path), 'cannot read JSON')

    def test_missing_arrays_and_invalid_json_envelopes(self) -> None:
        for path, option, content in (
            (self.gold_path, '--gold', []),
            (self.gold_path, '--gold', {}),
            (self.predictions_path, '--predictions', {}),
            (self.predictions_path, '--predictions', {'result': None}),
        ):
            with self.subTest(option=option, content=content):
                self.write_inputs()
                path.write_text(json.dumps(content), encoding='utf-8')
                self.assert_cli_error(self.run_cli(*self.inputs()), option, str(path))

    def test_output_directory_must_exist(self) -> None:
        output = self.directory / 'missing' / 'metrics.json'
        self.assert_cli_error(self.run_cli(*self.inputs(), '--output', str(output)), '--output', str(output))

    def test_empty_predictions_preserve_false_negatives(self) -> None:
        self.predictions['issues'] = []
        self.write_inputs()
        result = self.run_cli(*self.inputs())
        self.assertEqual(result.returncode, 0, result.stderr)
        scored = json.loads(result.stdout)
        self.assertEqual((scored['overall']['tp'], scored['overall']['fp'], scored['overall']['fn']), (0, 0, 1))
        self.assertIsNone(scored['overall']['precision'])
        self.assertEqual(scored['overall']['recall'], 0)

    def test_authored_regressions_are_excluded_from_real_only_metrics(self) -> None:
        scored = evaluate(self.gold, [], real_only=True)
        self.assertEqual(scored['cases'], 2)
        self.assertEqual(scored['overall']['fn'], 1)
        all_cases = evaluate(self.gold, [], real_only=False)
        self.assertEqual(all_cases['cases'], 3)
        self.assertEqual(all_cases['overall']['fn'], 2)


if __name__ == '__main__':
    unittest.main()
