from __future__ import annotations

import unittest

from backend.app.literature.extractor import _select_page_text, extract_references
from backend.app.literature.normalize_references import normalize_text
from backend.app.literature.web_verifier import (
    _assert_web_search_performed,
    normalize_web_decision,
)


class LiteratureExtractionRegressionTests(unittest.TestCase):
    def test_native_order_wins_when_column_sort_corrupts_references_heading(self) -> None:
        sorted_text = "                 References                 likova, J.; Polev, K.\nCemri, M.; Pan, M. Z."
        native_text = "References\nCemri, M.; Pan, M. Z.\nZhidkovskaya, A.; Be-\nlikova, J.; Polev, K."

        selected, prefer_native_order = _select_page_text(sorted_text, native_text, False)

        self.assertTrue(prefer_native_order)
        self.assertEqual(native_text, selected)

    def test_appendix_heading_after_references_is_clipped(self) -> None:
        text = """Introduction
References
Dayu Yang, Antoine Simoulin, Xin Qian, Xiaoyi Liu,
Yuwei Cao, Zhaopu Teng, and Grey Yang. 2025.
Docagent:
A multi-agent system for automated
code documentation generation.
arXiv preprint arXiv:2504.08725.
7
A
Claim Extraction Prompts
This appendix summarizes the prompt families
"""

        mode, bibliography = extract_references(text)

        self.assertEqual("heading", mode)
        self.assertIn("A multi-agent system for automated", bibliography)
        self.assertNotIn("A Claim Extraction Prompts", bibliography)
        self.assertNotIn("This appendix summarizes", bibliography)

    def test_wrapped_reference_title_is_not_treated_as_tail_heading(self) -> None:
        text = """References
Dayu Yang, Antoine Simoulin, Xin Qian, Xiaoyi Liu,
Yuwei Cao, Zhaopu Teng, and Grey Yang. 2025.
Docagent:
A multi-agent system for automated
code documentation generation.
arXiv preprint arXiv:2504.08725.
"""

        mode, bibliography = extract_references(text)

        self.assertEqual("heading", mode)
        self.assertIn("Docagent:", bibliography)
        self.assertIn("A multi-agent system for automated", bibliography)
        self.assertIn("code documentation generation", bibliography)

    def test_author_year_references_without_hanging_indent_stay_whole(self) -> None:
        text = """References
Bloor, M.; Torraca, J.; Sandoval, I. O.; Ahmed, A.; White,
M.; Mercangöz, M.; and Mowbray, M. 2024. PC-Gym:
Benchmark Environments for Process Control Problems.
Lee, H.; Choi, D.; Kim, B.; Park, H.; and Kim, S. G.
2026. NRT-Bench: Benchmarking Multi-Turn Red-Teaming.
"""

        rows = normalize_text(text)

        self.assertEqual(2, len(rows))
        self.assertIn("White, M.; Mercangöz", rows[0]["reference"])
        self.assertIn("2026. NRT-Bench", rows[1]["reference"])

    def test_title_with_comma_is_not_mistaken_for_an_author(self) -> None:
        text = """References
Zheng, Y.; Mao, S.; Zhang, D.; and Cai, W. 2025. Reflex
First, Reflect Later: Latency-Aware Embodied LLM Agents
for Dynamic Response. arXiv preprint arXiv:2506.07223.
"""

        rows = normalize_text(text)

        self.assertEqual(1, len(rows))
        self.assertIn("Reflex First, Reflect Later", rows[0]["reference"])


class LiteratureWebRegressionTests(unittest.TestCase):
    def test_cp1251_mojibake_is_repaired(self) -> None:
        decision = normalize_web_decision(
            {"notes": "Р–РёРІРѕР№ РІРµР±-РїРѕРёСЃРє РЅРµ РІС‹РїРѕР»РЅРµРЅ."}
        )

        self.assertEqual("Живой веб-поиск не выполнен.", decision["notes"])

    def test_unavailable_browser_is_not_accepted_as_a_web_check(self) -> None:
        decision = normalize_web_decision(
            {"notes": "Живой веб-поиск не выполнен: браузеры недоступны."}
        )

        with self.assertRaisesRegex(RuntimeError, "не выполнил"):
            _assert_web_search_performed(decision)


if __name__ == "__main__":
    unittest.main()
