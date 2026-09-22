from __future__ import annotations

import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

from backend.app.literature.extractor import _select_page_text
from backend.app.literature.normalize_references import normalize_text
from backend.app.literature.web_verifier import (
    _assert_web_search_performed,
    normalize_web_decision,
    verify_reference_on_web,
)
from backend.app.llm.host_llm import HostLlmError, _decode_host_bridge_response


class LiteratureExtractionRegressionTests(unittest.TestCase):
    def test_native_order_wins_when_column_sort_corrupts_references_heading(self) -> None:
        sorted_text = "                 References                 likova, J.; Polev, K.\nCemri, M.; Pan, M. Z."
        native_text = "References\nCemri, M.; Pan, M. Z.\nZhidkovskaya, A.; Be-\nlikova, J.; Polev, K."

        selected, prefer_native_order = _select_page_text(sorted_text, native_text, False)

        self.assertTrue(prefer_native_order)
        self.assertEqual(native_text, selected)

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
    def test_bridge_envelope_missing_only_final_brace_is_recovered(self) -> None:
        response = {
            "request_id": "osa-edu-test",
            "model": "gpt-5.6-luna",
            "text": json.dumps({"status": "OK"}),
        }
        truncated = json.dumps(response, ensure_ascii=False)[:-1]

        self.assertEqual(response, _decode_host_bridge_response(truncated))

    def test_host_web_request_retries_invalid_bridge_json(self) -> None:
        first_error = HostLlmError(
            "Host bridge вернул некорректный JSON.",
            status=502,
            provider_code="host_bridge_invalid_response",
        )
        valid_response = json.dumps(
            {
                "status": "OK",
                "confidence": 0.99,
                "matched_title": "A verified paper",
                "matched_authors": ["A. Author"],
                "year": "2024",
                "venue": "A Journal",
                "identifier": "10.1000/test",
                "evidence_urls": ["https://doi.org/10.1000/test"],
                "search_queries": ["A verified paper"],
                "notes": "Источник подтверждён по странице DOI.",
            },
            ensure_ascii=False,
        )
        run = AsyncMock(side_effect=[first_error, valid_response])

        with (
            patch.dict(os.environ, {"LITERATURE_WEB_MAX_ATTEMPTS": "2"}),
            patch("backend.app.literature.web_verifier.run_host_llm", run),
            patch("backend.app.literature.web_verifier.asyncio.sleep", new=AsyncMock()),
        ):
            result = asyncio.run(
                verify_reference_on_web(
                    number="1",
                    original_citation="A. Author. A verified paper. 2024.",
                    cited_title="A verified paper",
                    model="gpt-5.6-luna",
                )
            )

        self.assertEqual(2, run.await_count)
        self.assertEqual(2, result["attempts"])
        self.assertEqual("OK", result["status"])

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
