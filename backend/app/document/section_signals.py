from __future__ import annotations

import regex as re


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower().replace("ё", "е")


def defense_heading_score(value: str) -> int:
    """Score whether a short heading explicitly introduces statements/results for defense.

    The detector uses semantic concept roots rather than enumerating surface phrases.
    It is intentionally conservative: a scientific-novelty section alone is not enough;
    an explicit defense/protection concept must be present.
    """
    text = _compact(value)
    if not text or len(text) > 260:
        return 0

    # Russian/English concept roots. These are semantic categories, not phrase templates.
    protection = bool(re.search(r"\bзащит\p{L}*\b|\bdefen[cs]\p{L}*\b", text, re.I))
    statement = bool(re.search(r"\bположени\p{L}*\b|\bstatement\p{L}*\b|\bthes[ei]s\b", text, re.I))
    statement_plural = bool(re.search(r"\bположения\b|\bположений\b|\bstatements\b|\bprovisions\b", text, re.I))
    result = bool(re.search(r"\bрезультат\p{L}*\b|\bresult\p{L}*\b|\bcontribution\p{L}*\b", text, re.I))
    submission = bool(re.search(
        r"\bвынос\p{L}*\b|\bпредстав\p{L}*\b|\bпредлаг\p{L}*\b|\bsubmit\p{L}*\b|\bpresent\p{L}*\b",
        text,
        re.I,
    ))
    submission_relation = bool(re.search(
        r"\b(?:на|к)\s+защит\p{L}*\b|\b(?:for|to)\s+(?:the\s+)?defen[cs]e\b",
        text,
        re.I,
    ))

    if not protection:
        return 0
    score = 2
    if statement_plural:
        score += 2
    elif statement:
        score += 1
    elif result and (submission or submission_relation):
        score += 1
    if submission or submission_relation:
        score += 1
    return score


def is_defense_heading(value: str) -> bool:
    return defense_heading_score(value) >= 4


def find_defense_heading_span(value: str) -> tuple[int, int] | None:
    """Locate an explicit defense-section marker anywhere in a PDF block.

    Extractors may merge a section marker with preceding prose or the first list
    item. Candidate clauses are built around the defense/protection concept and
    then judged by ``is_defense_heading``; no surface phrase is enumerated.
    """
    raw = str(value or "")
    if not raw.strip():
        return None
    for anchor in re.finditer(r"защит\p{L}*|defen[cs]\p{L}*", raw, re.I):
        left = raw.rfind("\n", 0, anchor.start())
        for mark in ".!?;":
            left = max(left, raw.rfind(mark, max(0, anchor.start() - 280), anchor.start()))
        start = left + 1 if left >= 0 else max(0, anchor.start() - 220)
        # Include a trailing colon because it often terminates the marker.
        candidates = []
        for match in re.finditer(r"[:\n]|[.!?;](?=\s|$)", raw[anchor.end():anchor.end() + 280]):
            candidates.append(anchor.end() + match.end())
        candidates.append(min(len(raw), start + 280))
        for end in sorted(set(candidates)):
            clause = raw[start:end].strip(" \t\r\n:.;")
            if re.match(r"^(?:\(?\d{1,3}\)?[.)]|[–—•-])\s+", clause):
                continue
            if is_defense_heading(clause):
                return (start, end)
    return None
