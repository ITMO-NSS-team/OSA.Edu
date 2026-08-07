from __future__ import annotations

import math
import regex as re
import unicodedata
from collections import Counter
from statistics import median
from typing import Any, Iterable


_PAGE_NUMBER_RE = re.compile(r"^[–—-]?\s*\d{1,4}\s*[–—-]?$")
_CAPTION_RE = re.compile(
    r"^(?:рис(?:унок)?|таблица|график|листинг|figure|fig\.?|table)\s*\.?\s*\d+(?:\.\d+)*\s*(?:[.\-–—:]|$)",
    re.I,
)
_LIST_RE = re.compile(r"^(?:\d{1,3}[.)]|[-–—•*])\s+", re.I)
_BIB_ITEM_RE = re.compile(r"^\d{1,3}[.)]\s+\S", re.I)
_SECTION_RE = re.compile(r"^(?:глава\s+)?\d{1,2}(?:\.\d{1,2}){1,3}\.?\s+\p{L}", re.I)
_CHAPTER_RE = re.compile(r"^(?:ГЛАВА|Chapter)\s+\d+\b")
_TOC_RE = re.compile(r"(?:\.\s*){5,}\s*\d+\s*$")
_BIB_START_RE = re.compile(r"^(?:список\s+(?:использованных\s+)?(?:источников|литературы)|references)\.?$", re.I)
_TOC_START_RE = re.compile(r"^(?:оглавление|содержание)\.?$", re.I)
_TOC_END_RE = re.compile(r"^(?:реферат|synopsis|введение)\.?$", re.I)
_BIB_END_RE = re.compile(
    r"^(?:список\s+иллюстративного\s+материала|список\s+рисунков|список\s+таблиц|"
    r"приложение\b|тексты\s+публикаций\b)",
    re.I,
)
_NAMED_HEADING_RE = re.compile(
    r"^(?:введение|заключение|реферат|аннотация|synopsis|abstract|"
    r"выводы(?:\s+по\s+главе(?:\s+\d+)?)?|оглавление|содержание|references|"
    r"список\s+(?:использованных\s+)?(?:источников|литературы|сокращений|"
    r"иллюстративного\s+материала|рисунков|таблиц)|словарь\s+терминов|"
    r"научная\s+новизна|цель(?:\s+работы)?|задачи(?:\s+(?:работы|исследования))?|"
    r"(?:основные\s+)?положения,?\s+выносимые\s+на\s+защиту|"
    r"актуальность(?:\s+темы(?:\s+исследования)?)?|методы\s+исследования|"
    r"объект\s+исследования|предмет\s+исследования|теоретическая\s+значимость|"
    r"практическая\s+значимость|достоверность|внедрение\s+результатов(?:\s+работы)?|"
    r"апробация(?:\s+результатов\s+работы|\s+работы)?|личный\s+вклад\s+автора|"
    r"структура\s+и\s+объ[её]м\s+диссертации|публикации\s+автора(?:\s+по\s+теме\s+диссертации)?|"
    r"основное\s+содержание\s+работы|приложение(?:\s+[А-ЯA-Z\d]+)?)\.?$",
    re.I,
)


def clean_font_name(value: str) -> str:
    return re.sub(r"^[A-Z]{6}\+", "", value or "")


def build_pdf_pages(document: Any) -> tuple[list[dict[str, Any]], dict[str, Any], list[int]]:
    """Extract a line-aware PDF block model.

    The model is deliberately conservative: page numbers and repeated marginal
    artefacts are separated before paragraphs are assembled, and all logical blocks
    retain their source lines and bounding boxes.
    """
    raw_pages: list[dict[str, Any]] = []
    all_sizes: list[float] = []
    font_stats: dict[str, int] = {}
    size_stats: dict[str, int] = {}
    empty_pages: list[int] = []

    for page_index, page in enumerate(document):
        number = page_index + 1
        fragments: list[dict[str, Any]] = []
        payload = page.get_text("dict", sort=True)
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        source_order = 0
        for raw_block_index, raw in enumerate(payload.get("blocks", [])):
            if raw.get("type") != 0:
                continue
            for raw_line_index, raw_line in enumerate(raw.get("lines", [])):
                fragment = _line_fragment(
                    raw_line,
                    page=number,
                    source_order=source_order,
                    raw_block_index=raw_block_index,
                    raw_line_index=raw_line_index,
                )
                source_order += 1
                if not fragment:
                    continue
                fragments.append(fragment)
                for span in fragment["spans"]:
                    length = max(1, len(str(span.get("text") or "").strip()))
                    size = float(span.get("fontSize") or 0)
                    if size > 0:
                        all_sizes.extend([size] * length)
                        key = f"{round(size, 1):g}"
                        size_stats[key] = size_stats.get(key, 0) + length
                    font = str(span.get("fontName") or "")
                    if font:
                        font_stats[font] = font_stats.get(font, 0) + length
        visual_lines = _coalesce_visual_lines(fragments, page_width, page_height)
        if not visual_lines:
            empty_pages.append(number)
        raw_pages.append({
            "number": number,
            "width": round(page_width, 2),
            "height": round(page_height, 2),
            "lines": visual_lines,
        })

    body_size = round(median(all_sizes), 2) if all_sizes else 0.0
    repeated_signatures = _repeated_margin_signatures(raw_pages)
    pages: list[dict[str, Any]] = []
    bibliography_active = False
    toc_active = False

    for raw_page in raw_pages:
        content_lines: list[dict[str, Any]] = []
        artifacts: list[dict[str, Any]] = []
        for line in raw_page["lines"]:
            role = _artifact_role(line, raw_page, repeated_signatures)
            if role:
                artifacts.append({**line, "layoutRole": role})
            else:
                content_lines.append(line)

        blocks, bibliography_active, toc_active = _merge_page_lines(
            content_lines,
            page_number=raw_page["number"],
            page_width=raw_page["width"],
            page_height=raw_page["height"],
            body_size=body_size,
            bibliography_active=bibliography_active,
            toc_active=toc_active,
        )
        page_text = "\n\n".join(block["text"] for block in blocks)
        pages.append({
            "number": raw_page["number"],
            "width": raw_page["width"],
            "height": raw_page["height"],
            "text": page_text,
            "blocks": blocks,
            "layoutArtifacts": artifacts,
        })

    style_stats = {
        "fonts": font_stats,
        "sizes": size_stats,
        "alignment": dict(Counter(
            block.get("alignment", "unknown")
            for page in pages
            for block in page.get("blocks", [])
        )),
        "bodyFontSize": body_size,
        "pdfLineCount": sum(len(page.get("lines", [])) for page in raw_pages),
        "pdfBlockCount": sum(len(page.get("blocks", [])) for page in pages),
        "layoutArtifactCount": sum(len(page.get("layoutArtifacts", [])) for page in pages),
    }
    return pages, style_stats, empty_pages


def _line_fragment(
    raw_line: dict[str, Any],
    *,
    page: int,
    source_order: int,
    raw_block_index: int,
    raw_line_index: int,
) -> dict[str, Any] | None:
    spans: list[dict[str, Any]] = []
    for raw_span_index, raw_span in enumerate(raw_line.get("spans", [])):
        text = unicodedata.normalize("NFC", str(raw_span.get("text") or ""))
        if not text.strip():
            continue
        bbox = _bbox(raw_span.get("bbox"))
        font = clean_font_name(str(raw_span.get("font") or ""))
        flags = int(raw_span.get("flags") or 0)
        size = float(raw_span.get("size") or 0)
        spans.append({
            "text": text,
            "bbox": bbox,
            "fontName": font or None,
            "fontSize": round(size, 2) if size else None,
            "bold": bool(flags & 16 or "bold" in font.lower() or "bx" in font.lower()),
            "italic": bool(
                flags & 2
                or "italic" in font.lower()
                or "oblique" in font.lower()
                or font.upper().startswith("SFTI")
            ),
            "flags": flags,
            "sourceSpanIndex": raw_span_index,
        })
    if not spans:
        return None
    bbox = _bbox(raw_line.get("bbox"))
    return {
        "page": page,
        "bbox": bbox,
        "spans": spans,
        "sourceOrder": source_order,
        "sourceBlockIds": [f"raw-{raw_block_index}-{raw_line_index}"],
        "direction": tuple(raw_line.get("dir") or (1.0, 0.0)),
    }


def _coalesce_visual_lines(
    fragments: list[dict[str, Any]],
    page_width: float,
    page_height: float,
) -> list[dict[str, Any]]:
    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda item: (item["bbox"][1], item["bbox"][0], item["sourceOrder"]))
    rows: list[list[dict[str, Any]]] = []
    for fragment in ordered:
        target: list[dict[str, Any]] | None = None
        for row in reversed(rows[-5:]):
            if _same_visual_row(row, fragment):
                target = row
                break
        if target is None:
            rows.append([fragment])
        else:
            target.append(fragment)

    lines: list[dict[str, Any]] = []
    for visual_order, row in enumerate(rows):
        row_spans = [span for fragment in row for span in fragment["spans"]]
        row_spans.sort(key=lambda span: (span["bbox"][0], span["bbox"][1]))
        logical_text, mapped_spans = _join_spans(row_spans)
        if not logical_text.strip():
            continue
        bbox = _bbox_union([fragment["bbox"] for fragment in row])
        line = _summarize_text_item(
            logical_text,
            mapped_spans,
            bbox,
            page_width=page_width,
        )
        line.update({
            "rawText": "".join(str(span.get("text") or "") for span in row_spans),
            "logicalText": logical_text,
            "text": logical_text,
            "sourceOrder": min(fragment["sourceOrder"] for fragment in row),
            "sourceBlockIds": [source for fragment in row for source in fragment["sourceBlockIds"]],
            "visualOrder": visual_order,
            "pageHeight": round(page_height, 2),
            "pageWidth": round(page_width, 2),
        })
        lines.append(line)
    return sorted(lines, key=lambda item: (item["bbox"][1], item["bbox"][0], item["sourceOrder"]))


def _same_visual_row(row: list[dict[str, Any]], fragment: dict[str, Any]) -> bool:
    row_bbox = _bbox_union([item["bbox"] for item in row])
    a0, a1 = row_bbox[1], row_bbox[3]
    b0, b1 = fragment["bbox"][1], fragment["bbox"][3]
    overlap = max(0.0, min(a1, b1) - max(a0, b0))
    min_height = max(1.0, min(a1 - a0, b1 - b0))
    center_delta = abs((a0 + a1) / 2 - (b0 + b1) / 2)
    tolerance = max(1.6, min_height * 0.35)
    return overlap / min_height >= 0.55 or center_delta <= tolerance


def _join_spans(spans: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    result = ""
    mapped: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None
    for span in spans:
        value = str(span.get("text") or "")
        if not value:
            continue
        if previous is not None and _needs_space(previous, span, result, value):
            result += " "
        start = len(result)
        result += value
        end = len(result)
        mapped.append({**span, "logicalStart": start, "logicalEnd": end})
        previous = span
    result = re.sub(r"[ \t]+", " ", result).strip()
    # Recalculate offsets after whitespace normalisation only when it changed.
    if mapped and result != "".join(str(span.get("text") or "") for span in spans).strip():
        cursor = 0
        remapped: list[dict[str, Any]] = []
        for span in mapped:
            token = re.sub(r"[ \t]+", " ", str(span.get("text") or "")).strip()
            if not token:
                continue
            pos = result.find(token, cursor)
            if pos < 0:
                pos = cursor
            remapped.append({**span, "logicalStart": pos, "logicalEnd": min(len(result), pos + len(token))})
            cursor = min(len(result), pos + len(token))
        mapped = remapped
    return result, mapped


def _needs_space(previous: dict[str, Any], current: dict[str, Any], built: str, value: str) -> bool:
    if not built or built[-1].isspace() or value[:1].isspace():
        return False
    gap = float(current["bbox"][0]) - float(previous["bbox"][2])
    size_values = [float(x) for x in (previous.get("fontSize"), current.get("fontSize")) if x]
    font_size = min(size_values) if size_values else 10.0
    threshold = max(0.65, font_size * 0.075)
    if gap <= threshold:
        return False
    if built[-1] in "([{«“/" or value[0] in ",.;:!?%)]}»”/":
        return gap > font_size * 0.42
    return True


def _summarize_text_item(
    text: str,
    spans: list[dict[str, Any]],
    bbox: tuple[float, float, float, float],
    *,
    page_width: float,
) -> dict[str, Any]:
    total = 0
    bold_chars = 0
    italic_chars = 0
    math_chars = 0
    sizes: list[float] = []
    fonts: Counter[str] = Counter()
    for span in spans:
        value = str(span.get("text") or "")
        length = max(1, len(value.strip()))
        total += length
        if span.get("bold"):
            bold_chars += length
        if span.get("italic"):
            italic_chars += length
        font = str(span.get("fontName") or "")
        if font:
            fonts[font] += length
        size = float(span.get("fontSize") or 0)
        if size:
            sizes.extend([size] * length)
        if re.search(r"(?:CM(?:MI|SY|EX)|STIX|Math|Symbol)", font, re.I):
            math_chars += length
    center = (bbox[0] + bbox[2]) / 2
    width = max(0.0, bbox[2] - bbox[0])
    if abs(center - page_width / 2) <= page_width * 0.035 and width <= page_width * 0.88:
        alignment = "center"
    elif bbox[0] >= page_width * 0.56:
        alignment = "right"
    else:
        alignment = "left"
    return {
        "bbox": bbox,
        "fontName": fonts.most_common(1)[0][0] if fonts else None,
        "fontSize": round(median(sizes), 2) if sizes else None,
        "bold": total > 0 and bold_chars / total >= 0.60,
        "boldRatio": round(bold_chars / total, 3) if total else 0.0,
        "italic": total > 0 and italic_chars / total >= 0.60,
        "italicRatio": round(italic_chars / total, 3) if total else 0.0,
        "mathRatio": round(math_chars / total, 3) if total else 0.0,
        "alignment": alignment,
        "spans": spans,
        "charCount": len(text),
    }


def _repeated_margin_signatures(pages: list[dict[str, Any]]) -> set[str]:
    page_count = len(pages)
    if page_count < 3:
        return set()
    signature_pages: dict[str, set[int]] = {}
    for page in pages:
        height = float(page["height"])
        for line in page.get("lines", []):
            y0, y1 = line["bbox"][1], line["bbox"][3]
            in_margin = y1 <= height * 0.075 or y0 >= height * 0.925
            if not in_margin or _PAGE_NUMBER_RE.fullmatch(line["text"].strip()):
                continue
            signature = _margin_signature(line["text"])
            if len(signature) < 4:
                continue
            signature_pages.setdefault(signature, set()).add(int(page["number"]))
    minimum = max(3, min(8, math.ceil(page_count * 0.05)))
    return {signature for signature, values in signature_pages.items() if len(values) >= minimum}


def _margin_signature(value: str) -> str:
    value = unicodedata.normalize("NFC", value).lower().replace("ё", "е")
    value = re.sub(r"\d+", "#", value)
    value = re.sub(r"[^\w#]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def _artifact_role(line: dict[str, Any], page: dict[str, Any], repeated: set[str]) -> str | None:
    value = line["text"].strip()
    width = float(page["width"])
    height = float(page["height"])
    x0, y0, x1, y1 = line["bbox"]
    centered = abs((x0 + x1) / 2 - width / 2) <= width * 0.18
    in_number_zone = y1 <= height * 0.105 or y0 >= height * 0.90
    if centered and in_number_zone and _PAGE_NUMBER_RE.fullmatch(value):
        return "page_number"
    signature = _margin_signature(value)
    if signature in repeated:
        return "header" if y1 <= height * 0.5 else "footer"
    return None


def _merge_page_lines(
    lines: list[dict[str, Any]],
    *,
    page_number: int,
    page_width: float,
    page_height: float,
    body_size: float,
    bibliography_active: bool,
    toc_active: bool,
) -> tuple[list[dict[str, Any]], bool, bool]:
    if not lines:
        return [], bibliography_active, toc_active
    body_left = _estimate_body_left(lines, body_size, page_width)
    current: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = []

    for line in lines:
        value = line["text"].strip()
        is_heading = _is_heading_line(line, body_size)
        if bibliography_active and _BIB_END_RE.match(value) and is_heading:
            bibliography_active = False
        if toc_active and _TOC_END_RE.match(value) and is_heading:
            toc_active = False
        kind = _line_kind(line, body_size, bibliography_active, toc_active)

        if current is None or _starts_new_block(current, line, kind, body_size, body_left, bibliography_active):
            if current is not None:
                blocks.append(_finalize_block(current, page_number, page_width, page_height, body_size))
            current = {"kind": kind, "lines": [line]}
        else:
            current["lines"].append(line)
            current["kind"] = _merged_kind(current["kind"], kind)

        if _BIB_START_RE.match(value) and is_heading:
            bibliography_active = True
        if _TOC_START_RE.match(value) and is_heading:
            toc_active = True

    if current is not None:
        blocks.append(_finalize_block(current, page_number, page_width, page_height, body_size))

    # The bibliography heading itself remains a heading. All following blocks are
    # explicitly typed as bibliography while the section is active.
    return blocks, bibliography_active, toc_active


def _estimate_body_left(lines: list[dict[str, Any]], body_size: float, page_width: float) -> float:
    candidates: list[float] = []
    tolerance = max(1.0, body_size * 0.13)
    for line in lines:
        size = float(line.get("fontSize") or 0)
        if size and body_size and abs(size - body_size) > tolerance:
            continue
        if line.get("alignment") != "left" or line["bbox"][0] > page_width * 0.35:
            continue
        if _is_formula_line(line) or _CAPTION_RE.match(line["text"]):
            continue
        candidates.append(round(float(line["bbox"][0]), 1))
    if not candidates:
        return page_width * 0.12
    counts = Counter(candidates)
    common = [value for value, _ in counts.most_common(6)]
    return min(common) if common else min(candidates)


def _line_kind(line: dict[str, Any], body_size: float, bibliography_active: bool, toc_active: bool) -> str:
    value = line["text"].strip()
    if bibliography_active:
        return "bibliography_start" if _BIB_ITEM_RE.match(value) else "bibliography"
    if toc_active or _TOC_RE.search(value):
        return "toc"
    if _CAPTION_RE.match(value):
        return "caption"
    if _is_heading_line(line, body_size):
        return "heading"
    if _LIST_RE.match(value):
        return "list"
    if _is_formula_line(line):
        return "formula"
    return "paragraph"


def _is_heading_line(line: dict[str, Any], body_size: float) -> bool:
    value = re.sub(r"\s+", " ", line["text"]).strip()
    if not value or len(value) > 220:
        return False
    if _NAMED_HEADING_RE.match(value) or _CHAPTER_RE.match(value):
        return True
    if _SECTION_RE.match(value):
        return bool(line.get("bold") or float(line.get("fontSize") or 0) >= body_size * 1.01)
    words = re.findall(r"[\p{L}\p{N}]+", value)
    size = float(line.get("fontSize") or 0)
    return bool(
        float(line.get("boldRatio") or 0) >= 0.90
        and 1 <= len(words) <= 18
        and size >= (body_size * 0.98 if body_size else size)
        and not _LIST_RE.match(value)
        and not _BIB_ITEM_RE.match(value)
        and not re.search(r"(?<=\p{Ll})[.!?]\s+(?=[А-ЯЁA-Z])", value)
        and not re.search(r"(?:doi|https?://|\bvol\.|\bpp?\.|//)", value, re.I)
    )


def _is_formula_line(line: dict[str, Any]) -> bool:
    value = line["text"]
    math_ratio = float(line.get("mathRatio") or 0)
    symbols = len(re.findall(r"[=≈≤≥∑∫√±∞∈∉⊂∪∩×·←→{}|⎧⎨⎩τ]", value))
    letters = len(re.findall(r"\p{L}", value))
    return bool(
        math_ratio >= 0.42
        or (symbols >= 3 and letters <= 28)
        or (re.fullmatch(r"\s*\(?\d+(?:\.\d+)*\)?\s*", value) is None and symbols >= 5)
    )


def _starts_new_block(
    current: dict[str, Any],
    line: dict[str, Any],
    kind: str,
    body_size: float,
    body_left: float,
    bibliography_active: bool,
) -> bool:
    previous = current["lines"][-1]
    current_kind = current["kind"]
    gap = float(line["bbox"][1]) - float(previous["bbox"][3])
    close_gap = gap <= max(4.0, body_size * 1.15)
    large_gap = gap > max(10.0, body_size * 1.75)

    if kind == "heading":
        if current_kind == "heading" and close_gap and _heading_lines_belong_together(current["lines"], line, body_size):
            return False
        return True
    if current_kind == "heading":
        return True

    if bibliography_active or current_kind.startswith("bibliography") or kind.startswith("bibliography"):
        if kind == "bibliography_start":
            return True
        return current_kind not in {"bibliography", "bibliography_start"} or large_gap

    if kind in {"toc", "caption", "list"}:
        return True
    if current_kind == "toc":
        return True
    if current_kind == "caption":
        if kind == "paragraph" and close_gap and line.get("alignment") == "center":
            return False
        return True
    if current_kind == "list":
        if kind == "list" or large_gap:
            return True
        first = current["lines"][0]
        first_x = float(first["bbox"][0])
        current_x = float(line["bbox"][0])
        prev_terminal = bool(re.search(r"[.!?][»\"')\]]?\s*$", previous["text"].strip()))
        starts_upper = bool(re.match(r"^[«\"(\[]?[А-ЯЁA-Z]", line["text"].strip()))
        if len(current["lines"]) > 1 and current_x <= first_x + max(3.0, body_size * 0.25) and prev_terminal and starts_upper:
            return True
        return False

    if kind == "formula":
        return current_kind != "formula"
    if current_kind == "formula":
        return kind != "formula" or large_gap

    if large_gap:
        return True
    if _line_ends_soft_hyphen(previous["text"]):
        return False

    # First-line indentation plus a completed previous sentence is the strongest
    # geometric signal for a new paragraph in common Russian thesis layouts.
    indent = float(line["bbox"][0]) - body_left
    prev_indent = float(previous["bbox"][0]) - body_left
    starts_upper = bool(re.match(r"^[«\"(\[]?[А-ЯЁA-Z]", line["text"].strip()))
    prev_terminal = bool(re.search(r"[.!?][»\"')\]]?\s*$", previous["text"].strip()))
    if indent >= max(8.0, body_size * 0.70) and prev_indent <= max(5.0, body_size * 0.35) and prev_terminal and starts_upper:
        return True
    return False


def _heading_lines_belong_together(current_lines: list[dict[str, Any]], line: dict[str, Any], body_size: float) -> bool:
    first = current_lines[0]
    text = " ".join(item["text"] for item in current_lines)
    if _CHAPTER_RE.match(text) or _CHAPTER_RE.match(first["text"]):
        return bool(line.get("bold"))
    if _NAMED_HEADING_RE.match(text):
        return False
    size_delta = abs(float(first.get("fontSize") or body_size) - float(line.get("fontSize") or body_size))
    return bool(line.get("bold") and first.get("bold") and size_delta <= max(0.8, body_size * 0.10))


def _merged_kind(current: str, incoming: str) -> str:
    if current == "bibliography_start" and incoming == "bibliography":
        return "bibliography_start"
    if current == "formula" or incoming == "formula":
        return "formula"
    return current


def _finalize_block(
    current: dict[str, Any],
    page_number: int,
    page_width: float,
    page_height: float,
    body_size: float,
) -> dict[str, Any]:
    lines = current["lines"]
    raw_text = "\n".join(line["rawText"] for line in lines)
    logical_text, text_map = _join_logical_lines(lines)
    spans = [span for line in lines for span in line.get("spans", [])]
    bbox = _bbox_union([line["bbox"] for line in lines])
    summary = _summarize_text_item(logical_text, spans, bbox, page_width=page_width)
    kind = current["kind"]
    block_type = {
        "bibliography_start": "bibliography",
        "bibliography": "bibliography",
    }.get(kind, kind)
    return {
        "text": logical_text,
        "logicalText": logical_text,
        "rawText": raw_text,
        "type": block_type,
        "page": page_number,
        "bbox": bbox,
        "bodyFontSize": body_size,
        "sourceOrder": min(line["sourceOrder"] for line in lines),
        "sourceBlockIds": [source for line in lines for source in line.get("sourceBlockIds", [])],
        "lineCount": len(lines),
        "lines": lines,
        "textMap": text_map,
        "pageWidth": page_width,
        "pageHeight": page_height,
        "layoutRole": "content",
        **{key: value for key, value in summary.items() if key not in {"spans", "bbox"}},
    }


def _join_logical_lines(lines: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    result = ""
    mapping: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        value = line["text"].strip()
        if not value:
            continue
        if result:
            if _line_ends_soft_hyphen(result):
                result = re.sub(r"(?:\u00ad|-\u00ad)$", "", result)
            elif result.endswith("-") and re.match(r"^[а-яёa-z]", value):
                # Preserve semantic compounds; only a soft hyphen is removed
                # unconditionally. A visible hyphen remains part of the word.
                pass
            else:
                result += " "
        start = len(result)
        result += value
        end = len(result)
        mapping.append({
            "start": start,
            "end": end,
            "page": line.get("page"),
            "bbox": line.get("bbox"),
            "sourceOrder": line.get("sourceOrder"),
        })
    return re.sub(r"[ \t]+", " ", result).strip(), mapping


def _line_ends_soft_hyphen(value: str) -> bool:
    return bool(re.search(r"(?:\u00ad|-\u00ad)$", value or ""))


def _bbox(value: Iterable[Any] | None) -> tuple[float, float, float, float]:
    values = list(value or (0, 0, 0, 0))[:4]
    values += [0] * (4 - len(values))
    return tuple(round(float(item), 2) for item in values)  # type: ignore[return-value]


def _bbox_union(values: list[tuple[float, float, float, float]]) -> tuple[float, float, float, float]:
    if not values:
        return (0.0, 0.0, 0.0, 0.0)
    return (
        round(min(value[0] for value in values), 2),
        round(min(value[1] for value in values), 2),
        round(max(value[2] for value in values), 2),
        round(max(value[3] for value in values), 2),
    )
