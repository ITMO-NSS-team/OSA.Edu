from __future__ import annotations

"""Document-level grounded facts shared by multiple rule engines.

The store is intentionally conservative: Python only records facts that can be
located in the extracted document.  Rule engines may project a small subset of
these facts into an LLM prompt, but the canonical copy is built once per check.
"""

import json
import regex as re
from typing import Any

from ..checking.abbreviation_audit import (
    abbreviation_list_block_ids,
    collect_abbreviation_definitions,
)


def _compact(value: Any, limit: int = 280) -> str:
    return " ".join(str(value or "").split())[:limit]


def _evidence_row(row: dict[str, Any]) -> dict[str, Any]:
    result = {
        "blockId": str(row.get("blockId") or ""),
        "definition": _compact(row.get("definition"), 260),
        "quote": _compact(row.get("quote"), 360),
    }
    if row.get("page") is not None:
        result["page"] = row.get("page")
    if row.get("location"):
        result["location"] = str(row.get("location"))
    return result


def _terms_section_block_ids(document: dict[str, Any]) -> set[str]:
    start_re = re.compile(r"^(?:термины\s+и\s+определения|глоссарий|terms\s+and\s+definitions|glossary)\b", re.I)
    end_re = re.compile(
        r"^(?:список|перечень)\s+(?:используемых\s+)?(?:сокращений|условных\s+обозначений)|"
        r"^(?:введение|реферат|аннотация|глава\s+\d+|introduction|abstract|chapter\s+\d+)\b",
        re.I,
    )
    result: set[str] = set()
    active = False
    for block in sorted(document.get("blocks") or [], key=lambda item: int(item.get("order", 0))):
        bid = str(block.get("id") or "")
        text = _compact(block.get("text"), 500)
        if not text:
            continue
        if start_re.match(text):
            active = True
            if bid:
                result.add(bid)
            continue
        if active and end_re.match(text):
            active = False
        if active and bid:
            result.add(bid)
    return result


def _collect_term_definitions(document: dict[str, Any]) -> list[dict[str, Any]]:
    ids = _terms_section_block_ids(document)
    if not ids:
        return []
    rows: list[dict[str, Any]] = []
    marker = re.compile(r"^\s*(?P<term>[^—–:\n]{2,100}?)\s*(?:—|–|:)\s*(?P<definition>\S.{2,})\s*$")
    for block in document.get("blocks") or []:
        if str(block.get("id") or "") not in ids:
            continue
        for line in str(block.get("text") or "").splitlines():
            match = marker.match(line)
            if not match:
                continue
            term = _compact(match.group("term"), 100).strip(" .;,-")
            definition = _compact(match.group("definition"), 320).strip(" .;,")
            if len(term) < 2 or len(definition) < 3:
                continue
            row = {
                "term": term,
                "definition": definition,
                "blockId": str(block.get("id") or ""),
                "quote": _compact(line, 420),
            }
            if block.get("page") is not None:
                row["page"] = block.get("page")
            if block.get("location"):
                row["location"] = block.get("location")
            rows.append(row)
    return rows


def build_document_fact_store(document: dict[str, Any]) -> dict[str, Any]:
    """Build the canonical grounded fact store once for a document."""
    list_ids = abbreviation_list_block_ids(document)
    all_definitions = collect_abbreviation_definitions(document)
    abbreviation_glossary: dict[str, list[dict[str, Any]]] = {}
    for token, definitions in all_definitions.items():
        grounded = [row for row in definitions if str(row.get("blockId") or "") in list_ids]
        if grounded:
            abbreviation_glossary[str(token).upper().replace("–", "-")] = [_evidence_row(row) for row in grounded[:4]]

    elements = []
    for element in (document.get("map") or {}).get("elements") or []:
        elements.append({
            "id": str(element.get("id") or ""),
            "type": str(element.get("type") or ""),
            "label": _compact(element.get("label"), 180),
            "startBlockId": str(element.get("startBlockId") or ""),
            "endBlockId": str(element.get("endBlockId") or ""),
            "state": str(element.get("state") or ""),
            "canonicalRole": str(element.get("canonicalRole") or ""),
        })

    title = (document.get("fields") or {}).get("title") or {}
    return {
        "schemaVersion": 1,
        "title": {
            "text": _compact(title.get("text"), 400),
            "blockId": str(title.get("id") or ""),
            **({"page": title.get("page")} if title.get("page") is not None else {}),
        },
        "structure": {"elements": elements},
        "abbreviationGlossary": {
            "present": bool(list_ids),
            "blockIds": sorted(list_ids),
            "definitions": abbreviation_glossary,
        },
        "termDefinitions": _collect_term_definitions(document),
    }


def abbreviation_is_listed(fact_store: dict[str, Any] | None, token: str) -> bool:
    if not isinstance(fact_store, dict):
        return False
    glossary = fact_store.get("abbreviationGlossary") or {}
    definitions = glossary.get("definitions") or {}
    key = str(token or "").upper().replace("–", "-")
    return bool(definitions.get(key))


def project_fact_store(fact_store: dict[str, Any] | None, keys: list[str] | tuple[str, ...] | set[str]) -> dict[str, Any]:
    """Return a compact rule-specific projection so prompts stay bounded."""
    if not isinstance(fact_store, dict):
        return {}
    wanted = {str(key) for key in keys if key}
    out: dict[str, Any] = {"schemaVersion": fact_store.get("schemaVersion", 1)}
    if "title" in wanted:
        out["title"] = fact_store.get("title") or {}
    if "structure" in wanted:
        out["structure"] = fact_store.get("structure") or {"elements": []}
    if "abbreviationGlossary" in wanted:
        glossary = fact_store.get("abbreviationGlossary") or {}
        out["abbreviationGlossary"] = {
            "present": bool(glossary.get("present")),
            "definitions": glossary.get("definitions") or {},
        }
    if "termDefinitions" in wanted:
        out["termDefinitions"] = list(fact_store.get("termDefinitions") or [])
    return out


def fact_store_prompt_text(fact_store: dict[str, Any] | None, keys: list[str] | tuple[str, ...] | set[str]) -> str:
    projection = project_fact_store(fact_store, keys)
    if len(projection) <= 1:
        return ""
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))
