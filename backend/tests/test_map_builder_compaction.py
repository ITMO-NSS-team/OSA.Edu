from __future__ import annotations

import unittest

from backend.app.document.map_builder import _fit_structure_message


class StructureMessageCompactionTests(unittest.TestCase):
    def test_large_document_keeps_every_block_boundary(self) -> None:
        blocks = [
            {
                "id": f"BLOCK-{index}",
                "location": f"page {index}",
                "page": index,
                "type": "paragraph",
                "text": f"BEGIN-{index} " + ("x" * 1000) + f" END-{index}",
            }
            for index in range(1, 21)
        ]

        message, text_limit = _fit_structure_message(blocks, 9000)

        self.assertLessEqual(len(message), 9000)
        self.assertIsNotNone(text_limit)
        for index in range(1, 21):
            self.assertIn(f"BLOCK BLOCK-{index}", message)
            self.assertIn(f"BEGIN-{index}", message)
            self.assertIn(f"END-{index}", message)

    def test_small_document_is_not_compacted(self) -> None:
        blocks = [{"id": "BLOCK-1", "text": "Short text", "type": "heading"}]

        message, text_limit = _fit_structure_message(blocks, 5000)

        self.assertIsNone(text_limit)
        self.assertIn("Short text", message)


if __name__ == "__main__":
    unittest.main()
