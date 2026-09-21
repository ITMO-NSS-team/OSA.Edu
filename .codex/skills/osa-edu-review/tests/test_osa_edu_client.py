from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

import pymupdf


SCRIPT = Path(__file__).parents[1] / "scripts" / "osa_edu_client.py"
SPEC = importlib.util.spec_from_file_location("osa_edu_client", SCRIPT)
assert SPEC and SPEC.loader
CLIENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLIENT)


class AppendixCutoffTests(unittest.TestCase):
    def test_trailing_appendix_is_removed_but_toc_entry_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            target = Path(directory) / "prepared" / "source.pdf"
            document = pymupdf.open()
            for index in range(10):
                page = document.new_page()
                text = f"Chapter page {index + 1}"
                if index == 0:
                    text = "Contents\nReferences ........ 7\nAppendix A. Publications ........ 8"
                elif index == 6:
                    text = "References\n1. Example reference."
                elif index == 7:
                    text = "Appendix A. Publications"
                elif index > 7:
                    text = "Embedded article text"
                page.insert_text((72, 72), text)
            document.save(source)
            document.close()

            result = CLIENT._prepare_pdf_without_appendices(source, target)

            self.assertEqual(10, result["original_page_count"])
            self.assertEqual(7, result["submitted_page_count"])
            self.assertEqual(8, result["cutoff_page"])
            prepared = pymupdf.open(target)
            try:
                self.assertEqual(7, len(prepared))
            finally:
                prepared.close()

    def test_missing_reliable_boundary_fails_closed(self) -> None:
        document = pymupdf.open()
        try:
            for _ in range(4):
                page = document.new_page()
                page.insert_text((72, 72), "Main text")
            with self.assertRaisesRegex(CLIENT.OsaEduError, "Не удалось надёжно"):
                CLIENT._appendix_cutoff(document)
        finally:
            document.close()


if __name__ == "__main__":
    unittest.main()
