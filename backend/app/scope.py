from __future__ import annotations

"""Central document-scope policy.

3.8 introduces one authoritative place for deciding which extracted blocks belong
to the main dissertation and which content role a checker is allowed to inspect.
The goal is precision: synopsis/front matter, bibliography, appendices and attached
publication reprints must not silently become evidence for ordinary language or
layout rules of the main work.
"""

from collections.abc import Iterable
import regex as re

MAIN_WORK_ELEMENT_TYPES = {"introduction", "chapter", "chapter_conclusions", "conclusion"}
CANONICAL_ELEMENT_TYPES = {"goal", "tasks", "defense_statements"}


def _index(document: dict) -> tuple[list[dict], dict[str, int]]:
    blocks = list(document.get("blocks") or [])
    return blocks, {str(block.get("id")): pos for pos, block in enumerate(blocks) if block.get("id")}


def mapped_ids(
    document: dict,
    element_types: Iterable[str],
    *,
    canonical_only: bool = False,
) -> set[str] | None:
    """Return block ids covered by selected DocumentMap elements.

    ``None`` means that no usable map was available and callers should fall back
    to their legacy behaviour. An empty set means a usable map exists but none of
    the requested section types are present.
    """
    blocks, index = _index(document)
    elements = (document.get("map") or {}).get("elements") or []
    if not blocks or not elements:
        return None
    wanted = set(element_types)
    result: set[str] = set()
    for element in elements:
        if element.get("type") not in wanted:
            continue
        if canonical_only and (
            element.get("canonicalRole") == "secondary_copy"
            or element.get("documentUnit") in {"secondary_front_matter", "synopsis"}
        ):
            continue
        start = index.get(str(element.get("startBlockId") or ""))
        end = index.get(str(element.get("endBlockId") or ""))
        if start is None or end is None or start > end:
            continue
        result.update(str(block.get("id")) for block in blocks[start : end + 1] if block.get("id"))
    return result


def main_work_ids(document: dict) -> set[str] | None:
    """Ids belonging to authored main-work sections.

    The abstract/referral synopsis is intentionally excluded. Canonical goal,
    tasks and defence statements physically inside the main introduction are
    already covered by the introduction range.
    """
    return mapped_ids(document, MAIN_WORK_ELEMENT_TYPES, canonical_only=True)


def title_ids(document: dict) -> set[str] | None:
    return mapped_ids(document, {"title"}, canonical_only=True)


def content_role(block: dict) -> str:
    kind = str(block.get("type") or "paragraph").lower()
    text = str(block.get("text") or "")
    if kind in {"bibliography", "toc"}:
        return kind
    if kind in {"formula", "figure", "table", "caption", "heading", "code"}:
        return kind
    if is_code_or_prompt(text):
        return "code_or_prompt"
    return "narrative" if kind in {"paragraph", "list"} else kind


def select_blocks(
    document: dict,
    *,
    unit: str = "main_work",
    roles: set[str] | None = None,
    types: set[str] | None = None,
) -> list[dict]:
    """Select blocks through the central scope policy, preserving source order."""
    blocks = list(document.get("blocks") or [])
    if unit == "main_work":
        ids = main_work_ids(document)
    elif unit == "title":
        ids = title_ids(document)
    elif unit == "all":
        ids = None
    else:
        ids = mapped_ids(document, {unit}, canonical_only=True)

    selected = blocks if ids is None else [b for b in blocks if str(b.get("id")) in ids]
    if types is not None:
        selected = [b for b in selected if str(b.get("type") or "paragraph") in types]
    if roles is not None:
        selected = [b for b in selected if content_role(b) in roles]
    return selected


def is_in_main_work(document: dict, block_id: str) -> bool:
    ids = main_work_ids(document)
    # Without a map preserve legacy permissive behaviour; a mapped document is
    # the production path and therefore receives strict scope enforcement.
    return True if ids is None else str(block_id) in ids


def is_code_or_prompt(value: str) -> bool:
    return bool(
        re.search(
            r"(?:Requirements:|Rules:|Generate ONLY|Return ONLY|No commentary|```|"
            r"^\s*(?:def|class|import|from\s+\w+\s+import|SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET)\b)",
            value,
            re.I | re.M,
        )
    )
