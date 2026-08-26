from __future__ import annotations

"""Fallback linker for legacy/incomplete Document Maps.

New maps obtain statement→chapter relations from the existing structure LLM call.
This module preserves the proven local matcher only as a no-extra-request fallback;
it must not become the primary semantic source for new runs.
"""

import regex as re

_SEMANTIC_STOP = {
    "метод", "метода", "методы", "алгоритм", "алгоритма", "модель", "модели", "моделей",
    "разработан", "разработанных", "качества", "работы", "глава", "экспериментальная",
    "оценка", "оценивания", "программного", "программный", "кода", "код", "результат",
    "результатов", "применение", "применения", "положения", "положение", "выносимые", "защиту",
}

def _semantic_tokens(value: str) -> set[str]:
    words = re.findall(r"[А-ЯЁа-яёA-Za-z]{4,}", value.lower().replace("ё", "е"))
    return {word for word in words if word not in _SEMANTIC_STOP}

def _chapter_semantic_text(chapter: dict) -> str:
    blocks = chapter.get("blocks", [])
    # Headings and semantic summary carry most of the signal, but using the full
    # chapter for token membership lets a position match an implementation or
    # validation chapter without hard-coding chapter numbers.
    return " ".join([str(chapter.get("label") or ""), *[str(block.get("text") or "") for block in blocks]])

def _semantic_sequence(value: str) -> list[str]:
    return [
        word for word in re.findall(r"[А-ЯЁа-яёA-Za-z]{4,}", value.lower().replace("ё", "е"))
        if word not in _SEMANTIC_STOP
    ]

def _longest_contiguous_overlap(left: list[str], right: list[str]) -> int:
    if not left or not right:
        return 0
    previous = [0] * (len(right) + 1)
    best = 0
    for ltoken in left:
        current = [0] * (len(right) + 1)
        for index, rtoken in enumerate(right, start=1):
            if ltoken == rtoken:
                current[index] = previous[index - 1] + 1
                best = max(best, current[index])
        previous = current
    return best


def _chapter_role_text(chapter: dict) -> str:
    blocks = chapter.get("blocks", [])
    # Classify the chapter by its own title/opening, not by arbitrary subsection
    # headings. A normal methodological chapter can contain an "experimental"
    # subsection without becoming a validation chapter as a whole.
    opening = [str(block.get("text") or "") for block in blocks[:2]]
    return re.sub(r"\s+", " ", " ".join([str(chapter.get("label") or ""), *opening]))


def _is_validation_chapter(chapter: dict) -> bool:
    text = _chapter_role_text(chapter)
    return bool(re.search(
        r"(?:валидаци\p{L}*|верификаци\p{L}*|экспериментальн\p{L}*\s+(?:оцен\p{L}*|провер\p{L}*|исследован\p{L}*)|"
        r"подтверждени\p{L}*\s+положени\p{L}*)",
        text,
        re.I,
    ))


def _is_implementation_chapter(chapter: dict) -> bool:
    text = _chapter_role_text(chapter)
    return bool(re.search(
        r"программн\p{L}*\s+(?:инструмент\p{L}*|систем\p{L}*)|реализаци\p{L}*|инструментальн\p{L}*|архитектур\p{L}*",
        text,
        re.I,
    ))

def _statement_chapter_score(statement: str, chapter: dict) -> float:
    st = _semantic_tokens(statement)
    if not st:
        return 0.0
    chapter_text = _chapter_semantic_text(chapter).lower().replace("ё", "е")
    ct = _semantic_tokens(chapter_text)
    overlap = st & ct
    if not overlap:
        return 0.0

    def weighted_ratio(tokens: set[str]) -> float:
        matched = st & tokens
        numerator = sum(1.0 + min(1.2, max(0, len(token) - 6) * 0.08) for token in matched)
        denominator = sum(1.0 + min(1.2, max(0, len(token) - 6) * 0.08) for token in st)
        return numerator / max(2.5, denominator)

    full_ratio = weighted_ratio(ct)
    anchor_text = " ".join([
        str(chapter.get("label") or ""),
        *[
            str(block.get("text") or "")
            for block in chapter.get("blocks", [])
            if block.get("type") == "heading"
        ],
    ]).lower().replace("ё", "е")
    anchor_ratio = weighted_ratio(_semantic_tokens(anchor_text))
    # Chapter/section headings are much more discriminative than incidental
    # mentions in prose. This fixes cases where a review or application chapter
    # happens to repeat most terms from a position.
    score = full_ratio * 0.38 + anchor_ratio * 0.82

    # Morphological wording can hide an otherwise near-exact result/title match
    # (e.g. «повысить устойчивость моделей» vs «повышение устойчивости моделей»).
    # Give a small semantic bonus only when both the result and the chapter title
    # contain the same distinctive concept pair.
    statement_text = statement.lower().replace("ё", "е")
    chapter_label_text = str(chapter.get("label") or "").lower().replace("ё", "е")
    improve_stability = r"(?:повыс\p{L}*|повыш\p{L}*)[^.!?]{0,70}устойчив\p{L}*"
    if re.search(improve_stability, statement_text, re.I) and re.search(improve_stability, chapter_label_text, re.I):
        score += 0.48

    result_prefix = re.split(r",\s*(?:отлича\p{L}*|характериз\p{L}*|основан\p{L}*)", statement, maxsplit=1, flags=re.I)[0]
    result_sequence = _semantic_sequence(result_prefix)
    heading_sequences = [
        _semantic_sequence(str(chapter.get("label") or "")),
        *[
            _semantic_sequence(str(block.get("text") or ""))
            for block in chapter.get("blocks", [])
            if block.get("type") == "heading"
        ],
    ]
    longest_phrase = max((_longest_contiguous_overlap(result_sequence, sequence) for sequence in heading_sequences), default=0)
    if longest_phrase >= 2:
        score += min(0.52, 0.13 * (longest_phrase - 1))

    # Experimental/validation/application chapters often repeat the wording of
    # every position. They are useful as secondary evidence but should not outrank
    # the chapter where the method/model is actually introduced solely because of
    # that repetition.
    if _is_validation_chapter(chapter):
        # A validation/application chapter can still be the primary chapter when
        # its title directly names the defended result. Do not penalize a strong
        # result-title match solely because the chapter also contains validation.
        result_prefix = re.split(r",\s*(?:отлича\p{L}*|характериз\p{L}*|основан\p{L}*)", statement, maxsplit=1, flags=re.I)[0]
        result_sequence = _semantic_sequence(result_prefix)
        heading_sequences = [
            _semantic_sequence(str(chapter.get("label") or "")),
            *[_semantic_sequence(str(block.get("text") or "")) for block in chapter.get("blocks", [])[:2] if block.get("type") == "heading"],
        ]
        result_overlap = max((_longest_contiguous_overlap(result_sequence, sequence) for sequence in heading_sequences), default=0)
        if result_overlap < 4:
            score *= 0.68
    return score

def _statement_number(statement: str) -> str:
    match = re.match(r"\s*(\d+)\.", statement)
    return match.group(1) if match else ""


def _chapter_opening(chapter: dict, limit: int = 14) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join([str(chapter.get("label") or ""), *[str(b.get("text") or "") for b in chapter.get("blocks", [])[:limit]]]),
    )


def _explicit_validation_anchor(statement: str, chapter: dict) -> bool:
    number = _statement_number(statement)
    if not number:
        return False
    escaped = re.escape(number)
    opening = _chapter_opening(chapter, 20)
    patterns = [
        rf"(?:валидаци\p{{L}}*|верификаци\p{{L}}*|экспериментальн\p{{L}}*\s+(?:провер\p{{L}}*|оцен\p{{L}}*)|подтвержда\p{{L}}*)[^.!?]{{0,110}}положени\p{{L}}*\s+{escaped}\b",
        rf"положени\p{{L}}*\s+{escaped}\b[^.!?]{{0,110}}(?:подтвержда\p{{L}}*|валидаци\p{{L}}*|верификаци\p{{L}}*)",
    ]
    return any(re.search(pattern, opening, re.I) for pattern in patterns)


def _explicit_statement_chapter_anchor(statement: str, chapter: dict) -> bool:
    number = _statement_number(statement)
    if not number:
        return False
    escaped = re.escape(number)
    opening = _chapter_opening(chapter)
    # Only self-references that describe where the proposition is developed are
    # allowed to force a primary chapter.  «Валидация/подтверждение Положения N»
    # is evidence for a validation role and must never override the development
    # chapter when a statement is validated in several later sections.
    strong_patterns = [
        rf"(?:обеспечива\p{{L}}*|соответству\p{{L}}*|формулиру\p{{L}}*)[^.!?]{{0,100}}положени\p{{L}}*\s+{escaped}\b",
        rf"положени\p{{L}}*\s+{escaped}\b[^.!?]{{0,100}}(?:обеспечива\p{{L}}*|соответству\p{{L}}*|формулиру\p{{L}}*)",
    ]
    if any(re.search(pattern, opening, re.I) for pattern in strong_patterns):
        return True
    dedicated = re.search(
        rf"посвящен\p{{L}}*[^.!?]{{0,55}}положени\p{{L}}*\s+{escaped}\b",
        opening,
        re.I,
    )
    if not dedicated:
        return False
    # «глава посвящена экспериментальной верификации Положения N» is a
    # validation anchor, not evidence that the result is developed there.
    return not re.search(r"валидаци\p{L}*|верификаци\p{L}*|экспериментальн\p{L}*", dedicated.group(0), re.I)


def _match_statement_to_chapters(statement: str, chapters: list[dict]) -> list[tuple[dict, float]]:
    statement_tokens = _semantic_tokens(statement)
    anchor_sets = []
    for chapter in chapters:
        anchor_text = " ".join([
            str(chapter.get("label") or ""),
            *[str(block.get("text") or "") for block in chapter.get("blocks", []) if block.get("type") == "heading"],
        ])
        anchor_sets.append(_semantic_tokens(anchor_text))
    frequency: dict[str, int] = {}
    for token_set in anchor_sets:
        for token in token_set:
            frequency[token] = frequency.get(token, 0) + 1

    explicit_primary = next((chapter for chapter in chapters if _explicit_statement_chapter_anchor(statement, chapter)), None)

    scored: list[tuple[dict, float]] = []
    for index, chapter in enumerate(chapters):
        score = _statement_chapter_score(statement, chapter)
        distinctive = statement_tokens & anchor_sets[index]
        if distinctive and statement_tokens:
            idf_match = sum(1.0 / max(1, frequency.get(token, 1)) for token in distinctive)
            idf_total = sum(1.0 / max(1, frequency.get(token, 1)) for token in statement_tokens)
            score += 0.48 * idf_match / max(1.0, idf_total)
        scored.append((chapter, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    if explicit_primary is not None:
        explicit_index = next((i for i, row in enumerate(scored) if row[0].get("id") == explicit_primary.get("id")), None)
        if explicit_index is not None:
            chapter, score = scored.pop(explicit_index)
            scored.insert(0, (chapter, max(score, 1.25)))

    if explicit_primary is None and scored and _is_validation_chapter(scored[0][0]):
        result_prefix = re.split(r",\s*(?:отлича\p{L}*|характериз\p{L}*|основан\p{L}*)", statement, maxsplit=1, flags=re.I)[0]
        result_sequence = _semantic_sequence(result_prefix)
        top_heading_sequences = [
            _semantic_sequence(str(scored[0][0].get("label") or "")),
            *[_semantic_sequence(str(block.get("text") or "")) for block in scored[0][0].get("blocks", [])[:2] if block.get("type") == "heading"],
        ]
        top_result_overlap = max((_longest_contiguous_overlap(result_sequence, sequence) for sequence in top_heading_sequences), default=0)

        def plausible_primary(row: tuple[dict, float]) -> bool:
            chapter, score = row
            if _is_validation_chapter(chapter):
                return False
            heading_sequences = [
                _semantic_sequence(str(chapter.get("label") or "")),
                *[_semantic_sequence(str(block.get("text") or "")) for block in chapter.get("blocks", []) if block.get("type") == "heading"],
            ]
            phrase = max((_longest_contiguous_overlap(result_sequence, sequence) for sequence in heading_sequences), default=0)
            # Validation chapters often repeat the complete proposition name and
            # therefore score artificially high. A non-validation chapter with a
            # strong result-name overlap is the better primary semantic scope.
            return score >= scored[0][1] * 0.65 or (phrase >= 3 and score >= max(0.12, scored[0][1] * 0.35))

        alternative_index = None
        if top_result_overlap < 4:
            alternative_index = next((
                index for index, row in enumerate(scored[1:], start=1) if plausible_primary(row)
            ), None)
        if alternative_index is not None:
            scored.insert(0, scored.pop(alternative_index))

    if not scored:
        return []
    if scored[0][1] <= 0:
        # Never fall back to position-by-index. If lexical evidence is absent, the
        # conservative choice is to make the complete chapter set available to
        # the semantic check instead of silently assigning the wrong chapter.
        return [(chapter, 0.0) for chapter in chapters]
    best = scored[0][1]
    selected = [scored[0]]
    statement_number = re.match(r"\s*(\d+)\.", statement)
    result_prefix = re.split(r",\s*(?:отлича\p{L}*|характериз\p{L}*|основан\p{L}*)", statement, maxsplit=1, flags=re.I)[0]
    result_sequence = _semantic_sequence(result_prefix)
    for candidate in scored[1:]:
        if len(selected) >= 2:
            break
        chapter, candidate_score = candidate
        chapter_text = _chapter_semantic_text(chapter)
        explicit_position = bool(
            statement_number
            and re.search(rf"положени\p{{L}}*\s+{re.escape(statement_number.group(1))}\b", chapter_text, re.I)
        )
        heading_sequences = [
            _semantic_sequence(str(chapter.get("label") or "")),
            *[
                _semantic_sequence(str(block.get("text") or ""))
                for block in chapter.get("blocks", [])
                if block.get("type") == "heading"
            ],
        ]
        long_result_phrase = max(
            (_longest_contiguous_overlap(result_sequence, sequence) for sequence in heading_sequences),
            default=0,
        )
        # Secondary chapters are accepted when the thesis explicitly ties the
        # position to that chapter (typical validation chapter), or when an almost
        # identical result name appears in another chapter heading.
        if explicit_position or (long_result_phrase >= 4 and candidate_score >= max(0.30, best * 0.78)):
            selected.append(candidate)
    return selected


def _statement_chapter_roles(statement: str, chapters: list[dict]) -> list[tuple[dict, float, str]]:
    if not chapters:
        return []
    matches = _match_statement_to_chapters(statement, chapters)
    if not matches:
        return []
    primary, primary_score = matches[0]
    if primary_score <= 0:
        # No semantic signal: preserve the conservative full-chapter fallback.
        return [(chapter, 0.0, "primary") for chapter in chapters]

    number_match = re.match(r"\s*(\d+)\.", statement)
    number = number_match.group(1) if number_match else ""
    scored = [(chapter, _statement_chapter_score(statement, chapter)) for chapter in chapters]
    best = max([primary_score, *[score for _chapter, score in scored]])
    rows: list[tuple[dict, float, str]] = [(primary, primary_score, "primary")]

    for chapter, score in sorted(scored, key=lambda item: item[1], reverse=True):
        if chapter.get("id") == primary.get("id"):
            continue
        chapter_text = _chapter_semantic_text(chapter)
        explicit_position = bool(number and re.search(rf"положени\p{{L}}*\s+{re.escape(number)}\b", chapter_text, re.I))
        explicit_validation = _explicit_validation_anchor(statement, chapter)
        is_validation = _is_validation_chapter(chapter)
        is_implementation = _is_implementation_chapter(chapter)

        if (is_validation or explicit_validation) and (explicit_position or explicit_validation or score >= max(0.16, best * 0.38)):
            rows.append((chapter, score, "validation"))
            continue
        if is_implementation and score >= max(0.14, best * 0.25):
            rows.append((chapter, score, "implementation"))

    order = {"primary": 0, "implementation": 1, "validation": 2}
    return sorted(rows, key=lambda item: (order[item[2]], -item[1]))

def _infer_result_kind(text: str) -> str:
    normalized = re.sub(r"^\s*\d+\.\s*", "", text).lower()
    if re.search(r"^классификаци|систематизаци", normalized):
        return "classification"
    if re.search(r"^бенчмарк|^набор\s+данных|^датасет|протокол\s+оцен", normalized):
        return "benchmark"
    if re.search(r"^программ(?:ный|ная)\s+(?:комплекс|система)|^фреймворк", normalized):
        return "software"
    if re.search(r"^модел", normalized):
        return "model"
    if re.search(r"^метод|^алгоритм|^технолог", normalized):
        return "method"
    return "other"

# Public aliases used by the semantic document layer.
statement_chapter_roles = _statement_chapter_roles
infer_result_kind = _infer_result_kind
