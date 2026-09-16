from __future__ import annotations

"""Document-level grounded facts shared by multiple rule engines.

The store is intentionally conservative: Python only records facts that can be
located in the extracted document.  Rule engines may project a small subset of
these facts into an LLM prompt, but the canonical copy is built once per check.
"""

import json
import math
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
        "confidence": 1.0,
        "source": "explicit_abbreviation_list",
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
    # A new check may reuse a document carrying an older run's cache.
    document = {k: v for k, v in document.items() if k != 'factStore'}
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
            "confidence": element.get("confidence", 0),
            "pages": element.get("pages") or [],
            "source": element.get("source") or "document_map",
        })

    facts, element_facts = _structural_facts(document)
    for name, ids in (("abbreviation_list", list_ids), ("glossary", _terms_section_block_ids(document))):
        rows = [b for b in document.get("blocks", []) if str(b.get("id")) in ids]
        facts[name] = _fact_from_blocks(rows, source="explicit_section")
    title_rows = fact_blocks({"facts": facts}, "title")
    title = title_rows[0] if title_rows else {}
    return {
        "schemaVersion": 2,
        "facts": facts,
        "elementFacts": element_facts,
        "title": {**facts["title"], "text": title.get("text", ""),
                  "blockId": title.get("id", ""), "page": title.get("page")},
        "structure": {"elements": elements},
        "abbreviationGlossary": {
            "present": bool(list_ids), "blockIds": sorted(list_ids),
            "definitions": abbreviation_glossary,
        },
        "abbreviationDefinitions": all_definitions,
        "termDefinitions": _collect_term_definitions(document),
        "notations": {"status": "not_processed", "entities": {}},
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
    for key in wanted:
        fact = (fact_store.get('facts') or {}).get(key)
        if fact:
            out[key] = {k: v for k, v in fact.items() if k not in {'blocks', 'candidates'}}
            if key not in {'document_text', 'main_text', 'chapter'}:
                out[key]['text'] = '\n'.join(b.get('text', '') for b in fact_blocks(fact_store, key))
    return out


def fact_store_prompt_text(fact_store: dict[str, Any] | None, keys: list[str] | tuple[str, ...] | set[str]) -> str:
    projection = project_fact_store(fact_store, keys)
    if len(projection) <= 1:
        return ""
    return json.dumps(projection, ensure_ascii=False, separators=(",", ":"))


MIN_FACT_CONFIDENCE = 0.8
STRUCTURAL_FACTS = {
    "title", "abstract", "introduction", "goal", "tasks", "defense_statements",
    "chapter", "chapter_conclusions", "conclusion", "bibliography", "appendices",
}
SINGLE_FACTS = {"title", "introduction", "goal", "tasks", "defense_statements", "conclusion", "bibliography"}


def _fact_from_blocks(blocks: list[dict], *, source: str, confidence: float = 1.0) -> dict:
    blocks = [b for b in blocks if str(b.get('text') or '').strip()]
    return {"status": "found" if blocks else "missing", "confidence": confidence if blocks else 0,
            "source": source, "blocks": blocks, "blockIds": [b["id"] for b in blocks],
            "pages": list(dict.fromkeys(b["page"] for b in blocks if b.get("page") is not None)),
            "candidates": []}


def _structural_facts(document: dict) -> tuple[dict, dict]:
    from .semantic_ranges import trim_blocks_for_element
    from .title import extract_best_title
    from ..util import normalized_quote

    blocks = list(document.get("blocks") or [])
    index = {str(b.get("id")): i for i, b in enumerate(blocks)}
    elements = list((document.get("map") or {}).get("elements") or [])
    by_type = {name: [] for name in STRUCTURAL_FACTS}
    secondary_by_type = {name: [] for name in STRUCTURAL_FACTS}
    heading_only_by_type = {name: [] for name in STRUCTURAL_FACTS}
    element_facts = {}
    for el in elements:
        name = el.get("type")
        if name not in by_type:
            continue
        role = el.get("canonicalRole")
        is_secondary = role == "secondary_copy"
        is_heading_only = role == "main_heading_only"
        start, end = index.get(el.get("startBlockId")), index.get(el.get("endBlockId"))
        raw = blocks[start:end + 1] if start is not None and end is not None and start <= end else []
        selected = trim_blocks_for_element(name, raw)
        try:
            confidence = float(el.get('confidence') or 0)
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                confidence = 0
        except (ValueError, TypeError):
            confidence = 0
        row = _fact_from_blocks(selected, source=str(el.get("source") or "document_map"), confidence=confidence)
        row["elementId"] = el.get("id")
        row["state"] = el.get("state")
        if not raw:
            row["status"] = "not_processed"
        elif el.get("state") != "confirmed" or confidence < MIN_FACT_CONFIDENCE:
            row["status"] = "ambiguous"
        if name == "title" and raw:
            # Only grounded map text or extraction INSIDE the mapped interval.
            # Never search the full document or reuse an old analyzer guess.
            joined = " ".join(b.get("text", "") for b in raw)
            values = [str(el.get(k) or "").strip() for k in ("label", "quote")]
            text = next((v for v in values if len(v.split()) >= 2
                         and normalized_quote(v) in normalized_quote(joined)), "")
            found = {**raw[0], "text": text} if text else extract_best_title(raw, raw)
            row["blocks"] = [found] if found else []
            if not found:
                row["status"] = "ambiguous"
            elif el.get("quote") and el.get("label") and text == el.get("label"):
                quote = normalized_quote(str(el["quote"]))
                if quote in normalized_quote(joined) and normalized_quote(text) not in quote and quote not in normalized_quote(text):
                    row["status"] = "conflict"
        if is_heading_only:
            # Preserve the mapped heading for diagnostics, but do not let a bare
            # heading conflict with a substantive synopsis fallback.  If no
            # fallback exists, it will be reintroduced below as ambiguous rather
            # than mistaken for a real set of propositions.
            row["status"] = "ambiguous"
            heading_only_by_type[name].append(row)
        elif is_secondary:
            secondary_by_type[name].append(row)
        else:
            by_type[name].append(row)
        element_facts[str(el.get("id"))] = row

    for name, rows in heading_only_by_type.items():
        if not by_type[name] and rows:
            by_type[name] = rows

    def informative_goal(row: dict) -> bool:
        text = " ".join(str(b.get("text") or "") for b in row.get("blocks") or [])
        text = re.sub(
            r'^\s*Цель(?:\s+(?:(?:диссертационной\s+)?работы|исследования))?\s*[.:]?\s*',
            '',
            text,
            flags=re.I,
        ).strip()
        words = re.findall(r'[А-ЯЁа-яёA-Za-z]{2,}', text)
        return len(words) >= 5 and len(text) >= 35

    # A common thesis layout contains a substantive goal in the synopsis and a
    # heading-only ``Цель работы.`` in the main introduction/template.  The
    # canonical map may intentionally mark the first one as a secondary copy.
    # A heading is not evidence of the goal itself, so recover one unique,
    # confirmed substantive secondary goal instead of sending only the heading
    # to semantic rules such as CORE-6-4.
    primary_goals = by_type.get("goal") or []
    if not any(row.get("status") == "found" and informative_goal(row) for row in primary_goals):
        secondary_goals = [
            row for row in secondary_by_type.get("goal") or []
            if row.get("status") == "found" and informative_goal(row)
        ]
        distinct_secondary = {
            normalized_quote(" ".join(str(b.get("text") or "") for b in row.get("blocks") or []))
            for row in secondary_goals
        }
        if len(secondary_goals) == 1 or len(distinct_secondary) == 1:
            fallback = dict(max(secondary_goals, key=lambda row: float(row.get("confidence") or 0)))
            fallback["source"] = "document_map_secondary_fallback"
            fallback["recoveredFromSecondary"] = True
            fallback["primaryCandidates"] = primary_goals
            by_type["goal"] = [fallback]
        elif secondary_goals and primary_goals:
            for row in primary_goals:
                row["status"] = "ambiguous"
                row["fallbackCandidates"] = secondary_goals

    facts = {}
    for name, candidates in by_type.items():
        distinct = {(tuple(row['blockIds']), tuple(b.get('text', '') for b in row['blocks'])) for row in candidates}
        rows = list({b["id"]: b for row in candidates for b in row["blocks"]}.values())
        fact = _fact_from_blocks(rows, source="document_map",
                                 confidence=min((r["confidence"] for r in candidates), default=0))
        fact["candidates"] = candidates
        statuses = {r["status"] for r in candidates}
        if "conflict" in statuses or (name in SINGLE_FACTS and len(distinct) > 1):
            fact["status"] = "conflict"
        elif "not_processed" in statuses:
            fact["status"] = "not_processed"
        elif "ambiguous" in statuses or ('missing' in statuses and rows):
            fact["status"] = "ambiguous"
        facts[name] = fact
    facts["document_text"] = _fact_from_blocks(blocks, source="extraction")
    if not blocks:
        facts["document_text"]["status"] = "not_processed"
    elif document.get('technicalIncomplete') or document.get('extractionIncomplete'):
        facts['document_text']['status'] = 'not_processed'
    major = [r for name in ("introduction", "chapter", "chapter_conclusions", "conclusion") for r in by_type[name]]
    main_blocks = list({b["id"]: b for r in major for b in r["blocks"]}.values())
    facts["main_text"] = _fact_from_blocks(main_blocks, source="document_map",
                                           confidence=min((r["confidence"] for r in major), default=0))
    if any(r["status"] != "found" for r in major):
        facts["main_text"]["status"] = "ambiguous"
    if any(facts[n]["status"] == "conflict" for n in ("introduction", "chapter", "conclusion")):
        facts["main_text"]["status"] = "conflict"
    if facts['main_text']['status'] == 'found' and not any(
        b.get('type') in {'paragraph', 'list'} and str(b.get('text') or '').strip() for b in main_blocks
    ):
        facts['main_text']['status'] = 'missing'
    return facts, element_facts


def fact_blocks(store: dict, name: str) -> list[dict]:
    fact = (store.get("facts") or {}).get(name) or {}
    return list(fact.get("blocks") or []) if fact.get("status") == "found" else []


def notation_classification(store: dict | None, token: str) -> str:
    """One classification for all consumers, including legacy rule adapters."""
    key = token.upper().replace('–', '-')
    entities = ((store or {}).get('notations') or {}).get('entities') or {}
    values = {row['facts'].get('isAbbreviation', 'uncertain') for row in entities.values()
              if str(row.get('term') or '').upper().replace('–', '-') == key}
    if len(values) > 1 or 'uncertain' in values:
        return 'uncertain'
    if values:
        return next(iter(values))
    return 'yes' if abbreviation_is_listed(store, token) else 'uncertain'
