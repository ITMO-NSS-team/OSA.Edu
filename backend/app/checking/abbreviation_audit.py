from __future__ import annotations

"""High-recall abbreviation-like candidate enumeration.

3.9.3-rc2 intentionally keeps semantic judgement out of Python.  Python only
builds a broad, grounded inventory and annotates where each token was seen.
The LLM later decides whether the token is actually an abbreviation and whether
it violates CORE-4-1 / CORE-4-2 / CORE-4-3 / CORE-12.

The collector therefore prefers recall over precision:
- all-caps Cyrillic/Latin tokens;
- mixed-case technical tokens with multiple capitals (LoRA, IoU, eGFR, PyTorch);
- acronym/version/metric-like tokens containing digits, hyphens, slashes or @;
- obvious metric notation such as pass3 / pass@k / Recall@10.

Only typographic noise that cannot reasonably be a normative abbreviation is
removed here (section words, long all-caps Russian heading words, Roman numerals).
"""

from typing import Any
import regex as re

from .common import (
    contents_page_range,
    contextual,
    evidence,
    formula_like_block,
    is_likely_table_context,
    mapped_excluded_ids,
)
from ..scope import content_role, main_work_ids

# Broad tokenization first; `_looks_abbreviation_like` decides whether a lexical
# token is worth sending to the LLM.  Keeping this generic makes the collector
# domain-independent rather than a curated list of known abbreviations.
RAW_TOKEN_RE = re.compile(
    r"(?<![\p{L}\p{N}_])(?:"
    # Acronym + mixed-case named compound. Match it before the shorter
    # acronym branch so the lexical unit is preserved as a whole.
    r"[A-ZА-ЯЁ]{2,12}[-–][A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё0-9]{2,24}"
    r"|"
    # Classic acronym, including acronym-acronym / acronym-version compounds.
    r"[A-ZА-ЯЁ]{2,20}(?:[-–/][A-ZА-ЯЁ0-9]{1,20})*(?:@[A-Za-z0-9]{1,10})?(?:\d{1,4})?"
    r"|[A-ZА-ЯЁ]{1,12}\d{1,4}[A-Za-z0-9.]*"
    # Mixed-case technical token; predicate below requires >=2 capitals.
    r"|[A-Za-zА-ЯЁа-яё]{2,20}"
    r"|[A-Za-z][A-Za-z0-9]{2,20}"
    # Metric/test notation that may be lower-case.
    r"|pass(?:@?[A-Za-z0-9]+)|Recall@[A-Za-z0-9]+"
    r")(?![\p{L}\p{N}_])"
)


STOP = {
    "ГЛАВА", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК", "ЛИТЕРАТУРЫ", "ПРИЛОЖЕНИЕ",
    "РЕФЕРАТ", "ОГЛАВЛЕНИЕ", "СОДЕРЖАНИЕ", "ТАБЛИЦА", "РИСУНОК", "ВЫВОДЫ",
    "CONTENT", "CONTENTS", "ABSTRACT", "SYNOPSIS", "INTRODUCTION", "CONCLUSION",
    "REFERENCES", "PUBLICATIONS", "AUTHOR", "CONTRIBUTION", "CHAPTER",
}
ROMAN_RE = re.compile(
    r"^(?=[MDCLXVI]+$)M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$"
)
SPECIAL_LOWER_RE = re.compile(r"^(?:pass(?:@?[A-Za-z0-9]+)|recall@[A-Za-z0-9]+)$", re.I)


def _canonical(value: str) -> str:
    value = str(value or "").replace("–", "-").strip(".,;:()[]{}<>«»\"'")
    # PDF extraction often glues a Latin acronym to the following Russian word
    # (`APIинтерфейс`, `ASTпарсер`). Keep the acronym rather than inventing a
    # mixed-script candidate. This is lexical cleanup, not semantic filtering.
    glued = re.fullmatch(r"([A-Z]{2,20})([а-яё][А-ЯЁа-яё-]{2,})", value)
    if glued:
        return glued.group(1)
    return value


def _long_uppercase_word(value: str) -> bool:
    # PDF typography frequently turns ordinary Russian headings into ALL CAPS.
    # Long vowel-rich words are overwhelmingly ordinary words, not abbreviations.
    return bool(
        re.fullmatch(r"[А-ЯЁ]{4,}", value)
        and len(re.findall(r"[АЕЁИОУЫЭЮЯ]", value)) >= 2
    )


def _long_uppercase_heading_word(value: str) -> bool:
    """Conservative typography filter used only in title/TOC/heading roles.

    English theses frequently render normal heading words (METHODOLOGY,
    EVALUATION, GRADUATION) in all caps. Dropping such a word from an authored
    paragraph would hurt recall, so this heuristic is intentionally role-gated.
    """
    return bool(
        re.fullmatch(r"[A-Z]{6,}", value)
        and len(re.findall(r"[AEIOUY]", value)) >= 2
    )


def _occurrence_formula_like(text: str, start: int, end: int) -> bool:
    window = text[max(0, start - 70):min(len(text), end + 90)]
    marks = len(re.findall(r"[=≈≤≥∑∫√±∞∈∉⊂∪∩×·←→{}\[\]^_+*/]", window))
    # A variable-space declaration such as ``w ∈ R^L×d`` is formula context
    # even when the whole PDF block was classified as prose.
    if re.search(r"(?:∈|≈|≤|≥|=|×|\^)", window) and marks >= 2:
        return True
    # PDF extraction can flatten a displayed equation into prose and lose the
    # summation/fraction glyph while preserving Greek variables and an equation
    # number, e.g. ``1 XK (ri + δi). (3.16)``. This is still formula context.
    if re.search(r"[Α-Ωα-ωϕφδλμστθρ]", window) and re.search(r"[+*/=]|\(\d+(?:\.\d+)+\)", window):
        return True
    return marks >= 5


def _text_language(value: str) -> str:
    """Return a strong local language signal for one grounded context."""
    text = str(value or "")
    cyr = len(re.findall(r"[А-ЯЁа-яё]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    total = cyr + latin
    if total < 20:
        return "unknown"
    if latin / total >= .82:
        return "english"
    if cyr / total >= .72:
        return "russian"
    return "mixed"


_GLOSSARY_MARKER_RE = re.compile(
    r"(?<![\p{L}\p{N}_])([A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё0-9@+./-]{1,30})\s*(?:[—–-]|:|\t+)\s+"
)


def collect_abbreviation_definitions(document: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Extract document-local definitions without deciding rule verdicts.

    Dedicated abbreviation lists are often flattened into TOC/paragraph blocks
    and may contain many entries on one line. We therefore parse repeated
    ``TOKEN - expansion`` markers from every non-bibliography block and attach
    them as grounded context to matching inventory candidates.
    """
    result: dict[str, list[dict[str, Any]]] = {}
    for block in document.get("blocks", []):
        if str(block.get("type") or "").lower() in {"bibliography", "formula", "code", "figure", "table"}:
            continue
        text = str(block.get("text") or "")
        markers = list(_GLOSSARY_MARKER_RE.finditer(text))
        if not markers:
            continue
        # One marker is acceptable only when the local phrase itself looks like
        # an explicit definition; two or more markers strongly indicate a list.
        for idx, match in enumerate(markers):
            token = _canonical(match.group(1))
            if not _looks_abbreviation_like(token):
                continue
            stop = markers[idx + 1].start() if idx + 1 < len(markers) else min(len(text), match.end() + 320)
            definition = re.sub(r"\s+", " ", text[match.end():stop]).strip(" .;,")
            if len(definition) < 3 or len(re.findall(r"\p{L}", definition)) < 2:
                continue
            if len(markers) == 1 and len(definition) > 260:
                continue
            ev = evidence(block, contextual(text, match.start(), max(1, stop - match.start()), before=20, after=20))
            ev["token"] = token
            ev["definition"] = definition[:260]
            ev["contentRole"] = "abbreviation_list"
            key = token.upper().replace("–", "-")
            rows = result.setdefault(key, [])
            if not any(row.get("blockId") == ev.get("blockId") and row.get("definition") == ev.get("definition") for row in rows):
                rows.append(ev)
    return result


def _looks_abbreviation_like(value: str) -> bool:
    value = _canonical(value)
    if len(value) < 2 or len(value) > 35 or value.upper() in STOP:
        return False
    if ROMAN_RE.fullmatch(value.upper()):
        return False
    if _long_uppercase_word(value):
        return False
    if SPECIAL_LOWER_RE.fullmatch(value):
        return True

    letters = re.findall(r"\p{L}", value)
    if not letters:
        return False
    uppers = re.findall(r"[A-ZА-ЯЁ]", value)
    lowers = re.findall(r"[a-zа-яё]", value)
    digits = re.findall(r"\d", value)

    # Classic all-caps acronym / Cyrillic abbreviation.
    if len(uppers) >= 2 and not lowers:
        return True

    # Mixed-case technical names/abbreviations such as LoRA, IoU, eGFR, PyTorch.
    # Requiring at least two capitals avoids ordinary TitleCase words.
    if len(uppers) >= 2 and lowers:
        return True

    # One capital plus structured suffix is often a symbol/identifier.  We still
    # send it to the LLM because it is cheap to classify as not applicable.
    if len(uppers) >= 1 and digits and re.search(r"[-/@._]", value):
        return True
    if len(uppers) >= 1 and digits and len(value) <= 12:
        return True

    # Acronym-like compounds such as REST-API, AUC-IOU, USDL/OEWS.
    if len(uppers) >= 2 and re.search(r"[-/]", value):
        return True

    return False


def _abbreviation_list_block_ids(document: dict[str, Any]) -> set[str]:
    """Blocks that belong to an explicit abbreviation/glossary section.

    A token-definition shaped phrase in normal prose is not enough.  The range
    must be anchored by a dedicated list heading, which prevents PDF line-breaks
    around hyphens from turning ordinary narrative into glossary scope.
    """
    start_re = re.compile(
        r"^(?:список|перечень)\s+(?:используемых\s+)?(?:сокращений|условных\s+обозначений)(?:\s+и\s+условных\s+обозначений)?\b|"
        r"^(?:list|glossary)\s+of\s+(?:abbreviations|acronyms|symbols)\b",
        re.I,
    )
    end_re = re.compile(
        r"^(?:реферат|аннотация|введение|заключение|глава\s+\d+|abstract|introduction|conclusion|chapter\s+\d+)\b",
        re.I,
    )
    entry_re = re.compile(r"^[A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё0-9@+./-]{0,30}\s*[—–:\-]\s+\S")
    result: set[str] = set()
    active = False
    for block in document.get("blocks", []):
        bid = str(block.get("id") or "")
        text = " ".join(str(block.get("text") or "").split())
        if not text:
            continue
        if start_re.match(text):
            active = True
            if bid:
                result.add(bid)
            continue
        if active and (end_re.match(text) or (str(block.get("type") or "").lower() == "heading" and not entry_re.match(text))):
            active = False
        if active and bid:
            result.add(bid)
    return result


def abbreviation_list_block_ids(document: dict[str, Any]) -> set[str]:
    """Public read-only view of the explicit abbreviation-list scope."""
    return set(_abbreviation_list_block_ids(document))


def _is_table_header_context(blocks: list[dict[str, Any]], index: int) -> bool:
    """Return whether an extractor-labelled heading is a table column header.

    PDF extractors frequently label bold table headers as ``heading``. For
    CORE-4-2 that would incorrectly turn abbreviations in table columns into
    abbreviations in a work heading. Require both structural neighbours: a
    preceding table caption and following table-shaped content on the same page.
    This keeps actual section headings after a table in scope.
    """
    block = blocks[index]
    page = block.get("page")
    before = blocks[max(0, index - 3):index]
    has_table_caption = any(
        candidate.get("page") == page
        and (
            str(candidate.get("type") or "").lower() == "caption"
            or re.match(r"^\s*(?:таблица|table)\b", str(candidate.get("text") or ""), re.I)
        )
        for candidate in before
    )
    if not has_table_caption:
        return False

    for candidate in blocks[index + 1:index + 4]:
        if candidate.get("page") != page:
            break
        candidate_type = str(candidate.get("type") or "").lower()
        text = str(candidate.get("text") or "")
        if candidate_type in {"table", "formula"} or is_likely_table_context(text):
            return True
    return False


def _canonical_heading_scope(document: dict[str, Any], definitions: dict[str, list[dict[str, Any]]]) -> dict[str, str]:
    """Deterministic CORE-4-2 scope derived from document structure.

    The LLM classifies what a token *is*; Python owns where it occurs.  A block is
    heading-scope only when it is the canonical title, a real TOC block, a mapped
    section start, or an extractor heading inside the canonical main work.
    Explicit abbreviation-list entries are excluded even if PDF typography or a
    broad contents-page range labels them as TOC/heading.
    """
    blocks = list(document.get("blocks") or [])
    by_id = {str(b.get("id")): b for b in blocks if b.get("id")}
    title_ids = _title_block_ids(document)
    main_ids = main_work_ids(document)
    toc_pages = contents_page_range(document)
    definition_ids = _abbreviation_list_block_ids(document)
    scope: dict[str, str] = {}

    for bid in title_ids:
        if bid in by_id and bid not in definition_ids:
            scope[bid] = "title"

    # Map starts are authoritative section anchors, including cases where the
    # extractor lost the visual heading type.
    for element in (document.get("map") or {}).get("elements") or []:
        if element.get("canonicalRole") == "secondary_copy" or element.get("documentUnit") in {"secondary_front_matter", "synopsis"}:
            continue
        if element.get("type") not in {"introduction", "goal", "tasks", "defense_statements", "chapter", "chapter_conclusions", "conclusion"}:
            continue
        bid = str(element.get("startBlockId") or "")
        if bid and bid in by_id and bid not in definition_ids:
            scope.setdefault(bid, "heading")

    for index, block in enumerate(blocks):
        bid = str(block.get("id") or "")
        if not bid or bid in definition_ids:
            continue
        kind = str(block.get("type") or "").lower()
        if kind == "toc":
            # Extractors also assign ``toc`` to lists of figures/tables far from
            # the actual contents. CORE-4-2 names the work title and contents,
            # not every navigation-like appendix. When the contents span is
            # structurally known, it is authoritative; retain the old cautious
            # fallback only when extraction cannot establish that span at all.
            if not toc_pages or block.get("page") in toc_pages:
                scope.setdefault(bid, "toc")
            continue
        if block.get("page") in toc_pages:
            text = str(block.get("text") or "")
            # Contents pages can span across nearby front matter. Require a
            # structural TOC signal instead of treating every block on those
            # pages as a TOC entry.
            if re.search(r"(?:\.\s*){3,}\s*\d+\s*$", text) or re.search(r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s+)?[^\n]{2,180}\s+\d+\s*$", text):
                scope.setdefault(bid, "toc")
        if kind == "heading" and (main_ids is None or bid in main_ids):
            if _is_table_header_context(blocks, index):
                continue
            if not is_likely_table_context(str(block.get("text") or "")):
                scope.setdefault(bid, "heading")
    return scope


def _structural_heading_uses(document: dict[str, Any], definitions: dict[str, list[dict[str, Any]]], tokens: set[str]) -> dict[str, list[dict[str, Any]]]:
    scope = _canonical_heading_scope(document, definitions)
    result: dict[str, list[dict[str, Any]]] = {key: [] for key in tokens}
    for block in document.get("blocks", []):
        bid = str(block.get("id") or "")
        role = scope.get(bid)
        if not role:
            continue
        text = str(block.get("text") or "")
        for match in RAW_TOKEN_RE.finditer(text):
            raw = _canonical(match.group(0))
            key = raw.upper().replace("–", "-")
            if key not in tokens or not _looks_abbreviation_like(raw):
                continue
            if role in {"title", "toc", "heading"} and _long_uppercase_heading_word(raw):
                continue
            ev = _small_occurrence(block, text, match.start(), raw, role)
            rows = result.setdefault(key, [])
            if not any(x.get("blockId") == ev.get("blockId") for x in rows):
                rows.append(ev)
    return result


def _role_for_block(block: dict, *, toc_pages: set[int], title_ids: set[str], definition_block_ids: set[str] | None = None) -> str:
    bid = str(block.get("id") or "")
    if bid in (definition_block_ids or set()):
        return "abbreviation_list"
    if bid in title_ids:
        return "title"
    if str(block.get("type") or "").lower() == "toc":
        return "toc"
    if block.get("page") in toc_pages:
        text = str(block.get("text") or "")
        if re.search(r"(?:\.\s*){3,}\s*\d+\s*$", text) or re.search(r"(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s+)?[^\n]{2,180}\s+\d+\s*$", text):
            return "toc"
    role = content_role(block)
    text = str(block.get("text") or "")
    if role == "narrative" and formula_like_block(text):
        return "formula_like"
    if role == "narrative" and is_likely_table_context(text):
        return "table_like"
    return role


def _role_for_occurrence(block_role: str, text: str, start: int, end: int) -> str:
    if block_role == "narrative" and _occurrence_formula_like(text, start, end):
        return "formula_like"
    return block_role


def _title_block_ids(document: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    title = (document.get("fields") or {}).get("title")
    if isinstance(title, dict) and title.get("id"):
        ids.add(str(title["id"]))
    for element in (document.get("map") or {}).get("elements") or []:
        if element.get("type") != "title":
            continue
        for key in ("startBlockId", "endBlockId"):
            if element.get(key):
                ids.add(str(element[key]))
    return ids


def _small_occurrence(block: dict, text: str, start: int, token: str, role: str) -> dict:
    ev = evidence(block, contextual(text, start, len(token), before=110, after=190))
    ev["token"] = token
    ev["contentRole"] = role
    return ev


def collect_abbreviation_tokens(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return a broad grounded inventory, preserving a deterministic denominator.

    Candidate discovery is restricted to the canonical main work plus title/TOC,
    but not to narrative paragraphs.  Code/prompt/table/formula occurrences are
    kept and labelled so the LLM can decide that a rule is not applicable rather
    than Python silently dropping a potentially meaningful token.
    """
    excluded = mapped_excluded_ids(document)
    main_ids = main_work_ids(document)
    toc_pages = contents_page_range(document)
    title_ids = _title_block_ids(document)
    definitions = collect_abbreviation_definitions(document)
    definition_block_ids = _abbreviation_list_block_ids(document)
    found: dict[str, dict[str, Any]] = {}

    blocks = sorted(document.get("blocks", []), key=lambda item: int(item.get("order", 0)))
    full_text = "\n".join(str(block.get("text") or "") for block in blocks)
    for block in blocks:
        bid = str(block.get("id") or "")
        if not bid or bid in excluded or str(block.get("type") or "").lower() == "bibliography":
            continue

        # With a usable map, inspect the canonical main work plus title/TOC.
        # Without a map, preserve permissive legacy behaviour.
        in_scope = main_ids is None or bid in main_ids or bid in title_ids or block.get("page") in toc_pages
        if not in_scope:
            continue

        text = str(block.get("text") or "")
        if not text:
            continue
        block_role = _role_for_block(block, toc_pages=toc_pages, title_ids=title_ids, definition_block_ids=definition_block_ids)
        split_parts: set[str] = set()
        for broken in re.finditer(r"(?<![A-Z])([A-Z]{2,20})[-–]\s+([A-Z]{2,20})(?![A-Z])", text):
            left, right = broken.group(1), broken.group(2)
            concat = left + right
            hyphenated = left + "-" + right
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(concat)}(?![A-Za-z0-9])", full_text):
                split_parts.update({left, right})
            elif re.search(rf"(?<![A-Za-z0-9]){re.escape(hyphenated)}(?![A-Za-z0-9])", full_text):
                split_parts.update({left, right})

        for match in RAW_TOKEN_RE.finditer(text):
            raw = _canonical(match.group(0))
            if raw in split_parts:
                continue
            if not _looks_abbreviation_like(raw):
                continue
            role = _role_for_occurrence(block_role, text, match.start(), match.end())
            if role in {"title", "toc", "heading"} and _long_uppercase_heading_word(raw):
                continue
            # Do not emit an acronym prefix when the matcher can preserve the
            # following mixed-case named compound as one lexical token.
            if re.match(r"[-–][A-ZА-ЯЁ][A-Za-zА-ЯЁа-яё0-9]{2,24}", text[match.end():match.end()+30]):
                continue
            key = raw.upper().replace("–", "-")
            item = found.get(key)
            if item is None:
                item = {
                    "id": f"abbr-{len(found) + 1:04d}",
                    "token": raw,
                    "occurrenceCount": 0,
                    "roles": [],
                    "occurrences": [],
                    "listedDefinitions": list(definitions.get(key) or [])[:3],
                }
                found[key] = item
            item["occurrenceCount"] = int(item["occurrenceCount"]) + 1
            if role not in item["roles"]:
                item["roles"].append(role)
            # A few grounded contexts are enough for classification while keeping
            # the first request compact. Prefer diversity of blocks/roles.
            occurrence = _small_occurrence(block, text, match.start(), raw, role)
            existing = {(x.get("blockId"), x.get("contentRole")) for x in item["occurrences"]}
            if (occurrence.get("blockId"), role) not in existing and len(item["occurrences"]) < 6:
                item["occurrences"].append(occurrence)

    # Select the first meaningful authored use independently from structural
    # title/TOC/headings and from a dedicated abbreviation list. CORE-4-2 scope
    # is rebuilt separately from the structural map so LLM/classification cannot
    # accidentally turn glossary rows into heading violations.
    structural_headings = _structural_heading_uses(document, definitions, set(found.keys()))
    result: list[dict[str, Any]] = []
    structural_roles = {"title", "toc", "heading", "abbreviation_list"}
    for key, item in found.items():
        occurrences = item.pop("occurrences")
        narrative = next((x for x in occurrences if x.get("contentRole") == "narrative"), None)
        authored_non_heading = next((x for x in occurrences if x.get("contentRole") not in structural_roles), None)
        structural = next((x for x in occurrences if x.get("contentRole") in {"title", "toc", "heading"}), None)
        first = narrative or authored_non_heading or structural
        heading_uses = list(structural_headings.get(key) or [])[:3]
        context_uses = [x for x in occurrences if x is not first and x.get("contentRole") not in structural_roles][:2]
        local_text = " ".join(str((first or {}).get(field) or "") for field in ("quote", "context"))
        roles = [role for role in item.get("roles", []) if role != "abbreviation_list"]
        for ev in heading_uses:
            role = str(ev.get("contentRole") or "")
            if role and role not in roles:
                roles.append(role)
        result.append({
            **item,
            "roles": roles,
            "firstUse": first,
            "headingUses": heading_uses,
            "contextUses": context_uses,
            "contextLanguage": _text_language(local_text),
        })

    def order_key(item: dict[str, Any]) -> tuple[int, int, str]:
        ev = item.get("firstUse") or (item.get("headingUses") or [{}])[0]
        page = ev.get("page") if isinstance(ev, dict) else None
        start = ev.get("start") if isinstance(ev, dict) else None
        return (
            int(page) if isinstance(page, int) else 10**9,
            int(start) if isinstance(start, int) else 10**9,
            str(item.get("token") or "").upper(),
        )

    return sorted(result, key=order_key)
