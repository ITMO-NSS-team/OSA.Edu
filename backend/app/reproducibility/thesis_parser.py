"""Convert layout-aware OSA.Edu PDF blocks into OSA's PaperSection contract.

This is the lightweight (no Marker, OCR or paid remote parsing) input path.
Oversized physical sections are split on block boundaries so the downstream
claim selector does not receive an entire chapter as a single candidate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..extraction import extract_document

PROMPTS_DIR = Path(__file__).with_name("prompts")
MAX_SECTION_CHARS = 7000
SKIP_ROLES = {"header", "footer", "page_number"}


class TextLayerRequiredError(ValueError):
    pass


def _heading(block: dict[str, Any], text: str, body_size: float) -> bool:
    if block.get("type") == "toc":
        return False
    if block.get("type") in {"heading", "title"}:
        # Layout detectors can misclassify a short lowercase table cell
        # (e.g. "цель") as a document heading.
        if len(text.split()) <= 2 and text == text.lower() and not re.match(r"^\d+(?:\.\d+)*\b", text):
            return False
        return True
    return bool(
        block.get("type") == "paragraph"
        and len(text) <= 180
        and "\n" not in text
        and not text.endswith((".", ";", ":"))
        and body_size > 0
        and float(block.get("fontSize") or 0) >= body_size * 1.2
    )


def _bounded_blocks(text: str, limit: int) -> list[str]:
    """Split unusually large single blocks at whitespace, never drop source text."""
    if len(text) <= limit:
        return [text]
    words = re.findall(r"\S+|\s+", text)
    parts: list[str] = []
    buf = ""
    for word in words:
        if buf and len(buf) + len(word) > limit:
            parts.append(buf)
            buf = ""
        # Preserve extremely long unspaced text without an endless loop.
        if len(word) > limit:
            if buf:
                parts.append(buf)
                buf = ""
            parts.extend(word[i : i + limit] for i in range(0, len(word), limit))
        else:
            buf += word
    if buf:
        parts.append(buf)
    return parts


def document_sections(document: dict[str, Any], *, max_section_chars: int = MAX_SECTION_CHARS) -> list[dict[str, Any]]:
    if max_section_chars < 128:
        raise ValueError("max_section_chars must be >= 128")
    sizes = document.get("styleStats", {}).get("sizes", {})
    body_size = float(max(sizes, key=sizes.get)) if sizes else 0
    groups: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    skip_contents = False
    for block in document.get("blocks", []):
        text = str(block.get("text") or "").strip()
        if not text or block.get("layoutRole") in SKIP_ROLES or block.get("type") == "toc":
            continue
        is_heading = _heading(block, text, body_size)
        if is_heading and text.strip().upper() in {"ОГЛАВЛЕНИЕ", "СОДЕРЖАНИЕ", "TABLE OF CONTENTS"}:
            skip_contents = True
            current = None
            continue
        if is_heading:
            skip_contents = False
        if skip_contents:
            continue
        if current is None or is_heading:
            raw = text if is_heading else "Документ"
            numbering = re.match(r"^(?:глава\s+)?(\d+(?:\.\d+)*)(?:\.|\s)", raw, re.I)
            number = numbering.group(1) if numbering else None
            level = block.get("level") or (number.count(".") + 1 if number else 1)
            current = {
                "name": raw,
                "heading_meta": {"raw": raw, "level": max(1, min(6, int(level))), "numbering": number},
                "blocks": [],
            }
            groups.append(current)
        current["blocks"].extend(_bounded_blocks(text, max_section_chars))

    sections: list[dict[str, Any]] = []
    for group in groups:
        chunks: list[list[str]] = []
        active: list[str] = []
        size = 0
        for block_text in group["blocks"]:
            additional = len(block_text) + (2 if active else 0)
            if active and size + additional > max_section_chars:
                chunks.append(active)
                active = []
                size = 0
            active.append(block_text)
            size += len(block_text) + (2 if size else 0)
        if active:
            chunks.append(active)
        for index, chunk in enumerate(chunks, start=1):
            name = group["name"]
            if len(chunks) > 1:
                name = f"{name} (часть {index} из {len(chunks)})"
            sections.append({
                "section_id": f"s{len(sections) + 1:04d}",
                "name": name,
                "text": "\n\n".join(chunk),
                "heading_meta": dict(group["heading_meta"]),
            })
    return sections


def parse_thesis_pdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        raise ValueError("Для проверки воспроизводимости требуется PDF с текстовым слоем.")
    document = extract_document(path)
    pages = document.get("pages", [])
    empty_pages = [page["number"] for page in pages if not str(page.get("text") or "").strip()]
    sections = document_sections(document)
    long_text_pages = sum(1 for page in pages if len(str(page.get("text") or "").strip()) >= 80)
    total_chars = sum(len(item["text"]) for item in sections)
    mostly_scanned = len(pages) >= 5 and (long_text_pages < max(2, len(pages) // 5) or total_chars < 800)
    if mostly_scanned or not sections or not any(len(item["text"].strip()) >= 60 for item in sections):
        detail = " Страницы без текста: " + ", ".join(map(str, empty_pages)) + "." if empty_pages else ""
        raise TextLayerRequiredError(
            "Требуется PDF с текстовым слоем (text layer required)." + detail
            + " Сканированные страницы не поддерживаются локальным парсером; "
            "выберите Mistral OCR/Cloudflare AI при наличии подходящего доступа или загрузите распознанный PDF."
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
