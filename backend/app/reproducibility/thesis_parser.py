"""Convert OSA.Edu's PDF layout blocks to OSA's generic section contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..extraction import extract_document

PROMPTS_DIR = Path(__file__).with_name("prompts")


class TextLayerRequiredError(ValueError):
    pass


def document_sections(document: dict[str, Any]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sizes = document.get("styleStats", {}).get("sizes", {})
    body_size = float(max(sizes, key=sizes.get)) if sizes else 0
    for block in document.get("blocks", []):
        text = str(block.get("text") or "").strip()
        if not text or block.get("layoutRole") in {"header", "footer", "page_number"}:
            continue
        is_heading = block.get("type") in {"heading", "title"} or (
            block.get("type") == "paragraph" and len(text) <= 180
            and "\n" not in text and not text.endswith((".", ";", ":"))
            and body_size > 0 and float(block.get("fontSize") or 0) >= body_size * 1.2
        )
        if current is None or is_heading:
            raw = text if is_heading else "Документ"
            numbering = re.match(r"^(?:глава\s+)?(\d+(?:\.\d+)*)(?:\.|\s)", raw, re.I)
            number = numbering.group(1) if numbering else None
            level = block.get("level") or (number.count(".") + 1 if number else 1)
            current = {
                "section_id": f"s{len(sections) + 1:04d}",
                "name": raw,
                "text": "",
                "heading_meta": {"raw": raw, "level": max(1, min(6, int(level))), "numbering": number},
            }
            sections.append(current)
        # Preserve headings, tables, code and formulas as well as paragraphs.
        current["text"] += ("\n\n" if current["text"] else "") + text
    return sections


def parse_thesis_pdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("Для проверки воспроизводимости требуется PDF с текстовым слоем.")
    document = extract_document(path)
    pages = document.get("pages", [])
    empty_pages = [page["number"] for page in pages if not str(page.get("text") or "").strip()]
    sections = document_sections(document)
    if not sections:
        detail = ( " Страницы без текста: " + ", ".join(map(str, empty_pages)) + "." if empty_pages else "")
        raise TextLayerRequiredError(
            "Требуется PDF с текстовым слоем (text layer required)."
            + detail
            + " Сканированные страницы не поддерживаются; загрузите PDF с распознаваемым текстом."
        )
    return sections, document


def write_thesis_sections(path: Path, output_dir: Path) -> Path:
    sections, document = parse_thesis_pdf(path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "thesis_sections.json"
    destination.write_text(json.dumps({
        "sections": sections,
        "meta": {
            "source": {"paper": {"kind": "pdf", "path": str(path)}},
            "parser": "osa_edu_pymupdf_blocks",
            "block_model_version": document.get("blockModelVersion"),
            "warnings": document.get("warnings", []),
            "pages": len(document.get("pages", [])),
            "section_blocks": {
                block["id"]: {key: block[key] for key in ("page", "bbox", "type") if key in block}
                for block in document.get("blocks", [])
            },
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination
