from __future__ import annotations

from typing import Any
import regex as re

from .common import mapped_excluded_ids

# High-recall enumeration only. The LLM sees the whole document and decides
# whether each token is an abbreviation, a proper name, a model, a metric, etc.
# Keeping enumeration in Python gives us an exact coverage denominator.
TOKEN_RE = re.compile(
    r"(?<![\p{L}\p{N}_@-])"
    r"(?:"
    r"[A-ZА-ЯЁ]{2,14}(?:[-–/][A-ZА-ЯЁ0-9]{1,14})*(?:@[A-Za-z0-9]{1,8})?\d{0,4}"
    r"|[A-ZА-ЯЁ](?:[-–/][A-ZА-ЯЁ0-9]{2,14})+(?:@[A-Za-z0-9]{1,8})?"
    r"|[A-ZА-ЯЁ]\d{1,4}"
    r"|(?:GPT|Qwen|LLaMA|Gemma|Claude|Mistral)[-–]?[A-Za-z0-9.]{1,16}"
    r")"
    r"(?![\p{L}\p{N}_@])"
)

STOP = {
    "ГЛАВА", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК", "ЛИТЕРАТУРЫ", "ПРИЛОЖЕНИЕ",
    "РЕФЕРАТ", "ОГЛАВЛЕНИЕ", "СОДЕРЖАНИЕ", "ТАБЛИЦА", "РИСУНОК", "ВЫВОДЫ",
    "CONTENT", "CONTENTS", "ABSTRACT", "SYNOPSIS", "INTRODUCTION", "CONCLUSION",
    "REFERENCES", "PUBLICATIONS", "AUTHOR", "CONTRIBUTION",
}
ROMAN_RE = re.compile(r"^(?:I|II|III|IV|V|VI|VII|VIII|IX|X|XI|XII)$")


def _canonical(value: str) -> str:
    return value.replace("–", "-").strip()


def _long_uppercase_word(value: str) -> bool:
    # Typography can turn ordinary Russian heading words into all caps. Keep
    # shorter acronyms, but discard long vowel-rich words such as ФЕДЕРАЛЬНОЕ.
    return bool(
        re.fullmatch(r"[А-ЯЁ]{4,}", value)
        and len(re.findall(r"[АЕЁИОУЫЭЮЯ]", value)) >= 2
    )


def collect_abbreviation_tokens(document: dict[str, Any]) -> list[dict[str, Any]]:
    excluded = mapped_excluded_ids(document)
    found: dict[str, dict[str, Any]] = {}
    glossary_active = False
    glossary_heading = re.compile(r"^\s*(?:список|перечень)\s+(?:используемых\s+)?(?:сокращений|условных\s+обозначений)", re.I)
    glossary_entry = re.compile(r"^\s*([A-ZА-ЯЁ]{1,14}(?:[-–/][A-ZА-ЯЁ0-9]{1,14})*)\s*[—–-]\s+\S")
    next_major = re.compile(r"^\s*(?:введение|реферат|аннотация|abstract|introduction|глава\s+\d+|chapter\s+\d+)\b", re.I)

    for block in sorted(document.get("blocks", []), key=lambda item: int(item.get("order", 0))):
        if block.get("id") in excluded or block.get("type") in {"bibliography", "code", "formula", "figure"}:
            continue
        text = str(block.get("text") or "")
        compact = " ".join(text.split())
        if glossary_heading.search(compact):
            glossary_active = True
        elif glossary_active and block.get("type") == "heading" and next_major.search(compact):
            glossary_active = False

        for match in TOKEN_RE.finditer(text):
            raw = _canonical(match.group(0))
            key = raw.upper()
            if key in STOP or ROMAN_RE.fullmatch(key) or _long_uppercase_word(raw):
                continue
            item = found.get(key)
            if item is None:
                item = {
                    "id": f"abbr-{len(found) + 1:03d}",
                    "token": raw,
                    "firstBlockId": str(block.get("id") or ""),
                    "firstPage": block.get("page"),
                    "occurrenceCount": 0,
                    "listedInAbbreviationSection": False,
                    "introducedInParentheses": False,
                }
                found[key] = item
            item["occurrenceCount"] = int(item["occurrenceCount"]) + 1
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.end())
            if line_end < 0:
                line_end = len(text)
            line = text[line_start:line_end]
            entry = glossary_entry.match(line)
            if glossary_active and entry and _canonical(entry.group(1)).upper() == key:
                item["listedInAbbreviationSection"] = True
            before = text[max(0, match.start() - 220):match.start()]
            after = text[match.end():match.end() + 8]
            if re.search(r"[А-ЯЁа-яёA-Za-z][^().;:\n]{2,180}\(\s*$", before) and re.match(r"^\s*\)", after):
                item["introducedInParentheses"] = True
    return list(found.values())

