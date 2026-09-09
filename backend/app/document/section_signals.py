from __future__ import annotations

import regex as re


# This module intentionally describes semantic section concepts instead of exact
# document-specific headings.  The patterns are used only as conservative
# recovery signals after/around the LLM Document Map; context is still required
# before a range is materialized.


def _compact(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower().replace("ё", "е")


def _strip_numbering(value: str) -> str:
    return re.sub(r"^\s*(?:\d+(?:\.\d+)*\.?\s+|глава\s+\d+\.?\s*)", "", value, flags=re.I)


def _headingish(value: str) -> bool:
    text = _compact(value)
    if not text or len(text) > 280:
        return False
    # A colon is common for embedded headings such as «Цель: ...».  Long prose
    # without a colon/heading shape is deliberately rejected by the individual
    # scorers below.
    return len(text) <= 160 or ":" in text


def section_heading_score(section_type: str, value: str) -> int:
    """Return a conservative score for a canonical section concept.

    Scores are deliberately based on concept roots + structural wording rather
    than a list of exact surface phrases.  A caller should still use document
    position and neighbouring blocks before recovering a range.
    """
    text = _compact(value)
    if not text or len(text) > 320:
        return 0
    plain = _strip_numbering(text)

    if section_type == "introduction":
        return 5 if re.fullmatch(r"(?:введение|introduction)\s*[:.]?", plain, re.I) else 0

    if section_type == "goal":
        goal = bool(re.search(r"\b(?:цель|целью|aim|objective|goal)\b", plain, re.I))
        if not goal:
            return 0
        context = bool(re.search(r"\b(?:работ\p{L}*|исследован\p{L}*|диссертац\p{L}*|study|research|thesis)\b", plain, re.I))
        predicate = bool(re.search(r"\b(?:является|состоит\s+в|заключается\s+в|is|consists?\s+in)\b", plain, re.I))
        prefix = bool(re.match(r"^(?:основн\p{L}*\s+)?(?:цель|целью|aim|objective|goal)\b", plain, re.I))
        score = 2
        if prefix:
            score += 2
        if context:
            score += 1
        if predicate or ":" in plain or re.search(r"[—–-]", plain):
            score += 1
        return score if _headingish(plain) or prefix else 0

    if section_type == "tasks":
        direct = bool(re.search(r"\b(?:задач\p{L}*|tasks?|objectives?)\b", plain, re.I))
        prefix = bool(re.match(r"^(?:основн\p{L}*\s+)?(?:задач\p{L}*|research\s+tasks?|objectives?)\b", plain, re.I))
        attainment = bool(re.search(
            r"(?:для\s+достижения\s+(?:поставленн\p{L}*\s+)?цели.{0,140}(?:необходимо|следует|требуется|были?\s+поставлен\p{L}*|решить|выполнить)|"
            r"(?:to\s+achieve|for\s+achieving).{0,120}(?:goal|objective).{0,120}(?:tasks?|objectives?))",
            plain,
            re.I,
        ))
        if not (direct or attainment):
            return 0
        score = 2
        if prefix:
            score += 2
        if attainment:
            score += 2
        if ":" in plain or bool(re.search(r"\b(?:следующ\p{L}*|поставлен\p{L}*)\b", plain, re.I)):
            score += 1
        return score if _headingish(plain) or prefix or attainment else 0

    if section_type == "defense_statements":
        return defense_heading_score(value)

    if section_type == "chapter_conclusions":
        # Typical semantic variants include «Выводы по/к главе», «Итоги главы»,
        # «Основные результаты главы» and extended headings such as
        # «Ограничения методологии и выводы по главе».  An explicit
        # conclusions+chapter relation may occur after a qualifier; generic
        # «результаты главы ...» still must look like a heading.
        explicit_conclusions = bool(re.search(r"\bвывод\p{L}*\s+(?:к|по)\s+глав\p{L}*\b", plain, re.I))
        explicit_summary = bool(re.search(r"\bитог\p{L}*\s+глав\p{L}*\b|\b(?:chapter)\s+(?:summary|conclusions?)\b", plain, re.I))
        explicit_results = bool(re.search(r"\bосновн\p{L}*\s+результат\p{L}*\s+глав\p{L}*\b", plain, re.I))
        if (explicit_conclusions or explicit_summary or explicit_results) and len(plain) <= 220:
            return 5
        conclusion = bool(re.search(r"\b(?:вывод\p{L}*|итог\p{L}*|conclusions?|summary)\b", plain, re.I))
        chapter = bool(re.search(r"\b(?:глав\p{L}*|chapter)\b", plain, re.I))
        heading_prefix = bool(re.match(
            r"^(?:вывод\p{L}*|итог\p{L}*|conclusions?|summary)\b",
            plain,
            re.I,
        ))
        if not chapter or not conclusion or not heading_prefix:
            return 0
        return 4 if len(plain) <= 180 else 0

    if section_type == "conclusion":
        if re.fullmatch(r"(?:заключение|conclusion|conclusions)\s*[:.]?", plain, re.I):
            return 5
        if re.fullmatch(r"(?:общие|итоговые|основные)\s+выводы\s*[:.]?", plain, re.I):
            return 4
        return 0

    if section_type == "bibliography":
        if re.fullmatch(r"(?:список\s+(?:использованн\p{L}*\s+)?(?:литератур\p{L}*|источник\p{L}*)|литература|references|bibliography)\s*[:.]?", plain, re.I):
            return 5
        return 0

    if section_type == "appendices":
        if re.match(r"^(?:приложени\p{L}*|appendix|appendices)\b", plain, re.I):
            return 5
        return 0

    return 0


def is_section_heading(section_type: str, value: str) -> bool:
    thresholds = {
        "introduction": 5,
        "goal": 4,
        "tasks": 4,
        "defense_statements": 4,
        "chapter_conclusions": 4,
        "conclusion": 4,
        "bibliography": 5,
        "appendices": 5,
    }
    return section_heading_score(section_type, value) >= thresholds.get(section_type, 99)


def find_section_heading_span(section_type: str, value: str) -> tuple[int, int] | None:
    """Locate an inline canonical-section marker in a possibly merged PDF block."""
    if section_type == "defense_statements":
        return find_defense_heading_span(value)

    raw = str(value or "")
    if not raw.strip():
        return None

    anchors: dict[str, str] = {
        "goal": r"\b(?:цель|целью|aim|objective|goal)\b",
        "tasks": r"\b(?:задач\p{L}*|tasks?|objectives?|достижения\s+(?:поставленн\p{L}*\s+)?цели)\b",
        "chapter_conclusions": r"\b(?:вывод\p{L}*|итог\p{L}*|результат\p{L}*|conclusions?|summary|results?)\b",
        "conclusion": r"\b(?:заключение|conclusions?|общие\s+выводы|итоговые\s+выводы)\b",
        "bibliography": r"\b(?:список\s+(?:использованн\p{L}*\s+)?(?:литератур\p{L}*|источник\p{L}*)|литература|references|bibliography)\b",
        "appendices": r"\b(?:приложени\p{L}*|appendix|appendices)\b",
        "introduction": r"\b(?:введение|introduction)\b",
    }
    pattern = anchors.get(section_type)
    if not pattern:
        return None

    for anchor in re.finditer(pattern, raw, re.I):
        # Prefer a line/short clause around the concept.  This covers extractors
        # that merge a heading with preceding/following prose without scanning an
        # arbitrary long paragraph as a heading.
        line_start = raw.rfind("\n", 0, anchor.start()) + 1
        line_end = raw.find("\n", anchor.end())
        if line_end < 0:
            line_end = len(raw)
        candidates = [(line_start, line_end)]
        for sep in ".!?;":
            left = raw.rfind(sep, max(0, anchor.start() - 220), anchor.start())
            right = raw.find(sep, anchor.end(), min(len(raw), anchor.end() + 260))
            start = left + 1 if left >= 0 else max(0, anchor.start() - 180)
            end = right + 1 if right >= 0 else min(len(raw), anchor.end() + 220)
            candidates.append((start, end))
        for start, end in candidates:
            clause = raw[start:end].strip(" \t\r\n:.;")
            if is_section_heading(section_type, clause):
                return start, end
    return None


def defense_heading_score(value: str) -> int:
    """Score whether a short heading explicitly introduces statements/results for defense."""
    text = _compact(value)
    if not text or len(text) > 260:
        return 0

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
    implicit_plural_items = bool(re.search(
        r"\b(?:выносятся|представляются|предлагаются)\b|\bare\s+(?:submitted|presented)\b",
        text,
        re.I,
    )) and submission_relation

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
    if implicit_plural_items:
        score += 1
    return score


def is_defense_heading(value: str) -> bool:
    return defense_heading_score(value) >= 4


def find_defense_heading_span(value: str) -> tuple[int, int] | None:
    """Locate an explicit defense-section marker anywhere in a PDF block."""
    raw = str(value or "")
    if not raw.strip():
        return None
    for anchor in re.finditer(r"защит\p{L}*|defen[cs]\p{L}*", raw, re.I):
        left = raw.rfind("\n", 0, anchor.start())
        for mark in ".!?;":
            left = max(left, raw.rfind(mark, max(0, anchor.start() - 280), anchor.start()))
        start = left + 1 if left >= 0 else max(0, anchor.start() - 220)
        candidates = []
        for match in re.finditer(r"[:\n]|[.!?;](?=\s|$)", raw[anchor.end():anchor.end() + 280]):
            candidates.append(anchor.end() + match.end())
        candidates.append(min(len(raw), start + 280))
        for end in sorted(set(candidates)):
            clause = raw[start:end].strip(" \t\r\n:.;")
            if re.match(r"^(?:\(?\d{1,3}\)?[.)]|[–—•-])\s+", clause):
                continue
            if is_defense_heading(clause):
                return start, end
    return None
