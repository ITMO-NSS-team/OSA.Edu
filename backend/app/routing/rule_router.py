from __future__ import annotations

import json
import regex as re

from ..config import CONFIG_DIR
from ..document.numbered_items import collect_unique_numbered_items
from ..document.semantic_ranges import trim_blocks_for_element
from ..util import empty_usage

DIRECT_SELECTORS = {
    "title", "abstract", "introduction", "goal", "tasks", "defense_statements",
    "chapter", "chapter_conclusions", "conclusion", "bibliography", "appendices", "other",
}


def _load_config() -> dict:
    path = CONFIG_DIR / "rule-routing.json"
    if not path.exists():
        return {"rules": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"rules": {}}


def _unique_blocks(blocks: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for block in blocks:
        block_id = str(block.get("id", ""))
        if block_id and block_id not in seen:
            seen.add(block_id)
            result.append(block)
    return result


def _build_fragments(document: dict, map_value: dict) -> list[dict]:
    blocks = document.get("blocks", [])
    block_index = {block.get("id"): index for index, block in enumerate(blocks)}
    fragments: list[dict] = []

    # Direct map fragments. IDs intentionally stay equal to DocumentMap element IDs;
    # the React client and reports can therefore refer to exactly the same semantic ranges.
    for element in map_value.get("elements", []):
        start = block_index.get(element.get("startBlockId"))
        end = block_index.get(element.get("endBlockId"))
        if start is None or end is None or start > end:
            continue
        selected = trim_blocks_for_element(element.get("type", "other"), blocks[start:end + 1])
        if not selected:
            continue
        fragments.append({
            "id": element.get("id"),
            "type": element.get("type", "other"),
            "selector": element.get("type", "other"),
            "label": element.get("label") or element.get("type", "other"),
            "blocks": selected,
            "complete": element.get("state") == "confirmed",
        })

    def by_type(element_type: str) -> list[dict]:
        return [fragment for fragment in fragments if fragment.get("type") == element_type]

    def add_virtual(
        selector: str,
        label: str,
        source: list[dict],
        selected_blocks: list[dict],
        complete: bool | None = None,
        **metadata,
    ) -> None:
        unique = _unique_blocks(selected_blocks)
        if not unique:
            return
        if complete is None:
            complete = bool(source) and all(item.get("complete") for item in source)
        fragments.append({
            "id": f"virtual-{selector}",
            "type": "virtual",
            "selector": selector,
            "label": label,
            "blocks": unique,
            "complete": bool(complete),
            **metadata,
        })

    title = by_type("title")
    introduction = by_type("introduction")
    goal = by_type("goal")
    tasks = by_type("tasks")
    defense = by_type("defense_statements")
    chapters = by_type("chapter")
    explicit_conclusions = by_type("chapter_conclusions")
    conclusion = by_type("conclusion")

    derived_conclusions = explicit_conclusions or [
        result for index, chapter in enumerate(chapters)
        if (result := _derive_chapter_conclusion(chapter, index)) is not None
    ]
    existing_ids = {fragment.get("id") for fragment in fragments}
    for item in derived_conclusions:
        if item.get("id") not in existing_ids:
            fragments.append(item)
            existing_ids.add(item.get("id"))

    add_virtual(
        "title_goal",
        "Название и цель",
        [*title, *goal, *introduction],
        [*(_flatten_blocks(title)), *(_flatten_blocks(goal))],
    )

    scientific_source = [*title, *introduction, *goal, *tasks, *defense, *chapters, *conclusion]
    scientific_blocks = [
        *_flatten_blocks(title),
        *_flatten_blocks(introduction),
        *[block for chapter in chapters for block in _select_chapter_summary(chapter.get("blocks", []))],
        *_flatten_blocks(conclusion),
    ]
    if not scientific_blocks:
        scientific_blocks = [
            block for block in blocks
            if block.get("type") in {"paragraph", "list", "heading"}
            and block.get("type") not in {"bibliography", "toc"}
        ]
    add_virtual("scientific_core", "Научное ядро работы", scientific_source, scientific_blocks, bool(scientific_blocks))

    defense_chapter_blocks = [
        *_flatten_blocks(defense),
        *[block for chapter in chapters for block in _select_chapter_evidence(chapter.get("blocks", []))],
        *_flatten_blocks(derived_conclusions),
    ]
    add_virtual(
        "defense_chapters",
        "Положения, аналоги и соответствующие главы",
        [*defense, *chapters, *derived_conclusions],
        defense_chapter_blocks,
    )

    statements = _split_defense_statements(defense)
    if statements and defense:
        add_virtual(
            "defense_statements_complete",
            "Полные положения, выносимые на защиту",
            defense,
            _flatten_blocks(defense),
            all(item.get("complete") for item in defense),
            semanticContext="\n".join(
                f"Положение {index + 1}: {statement.get('text', '')}"
                for index, statement in enumerate(statements)
            ),
        )

    for index, statement in enumerate(statements):
        statement_text = statement.get("text", "")
        roles = _statement_chapter_roles(statement_text, chapters)
        primary_roles = [item for item in roles if item[2] == "primary"]
        # CORE-2-3/CORE-15 ask specifically for the chapter devoted to the
        # proposition. Validation/application chapters remain useful metadata but
        # must not inflate the evidence packet or masquerade as the primary chapter.
        selected_roles = primary_roles or roles
        selected_chapters = [chapter for chapter, _score, _role in selected_roles]
        if not selected_chapters:
            continue
        primary_labels = [chapter.get("label", "") for chapter in selected_chapters]
        chapter_blocks = [
            block
            for chapter in selected_chapters
            for block in chapter.get("blocks", [])
        ]
        role_rows = [
            {
                "chapterId": chapter.get("id"),
                "label": chapter.get("label", ""),
                "role": role,
                "score": round(score, 3),
            }
            for chapter, score, role in roles
        ]
        support_text = "; ".join(
            f"{row['role']}: {row['label']}" for row in role_rows if row["role"] != "primary"
        )
        semantic_context = (
            f"Проверяемое положение {index + 1}: {statement_text}\n"
            f"Основная глава для проверки аналогов/прототипа: {' + '.join(primary_labels)}."
        )
        if support_text:
            semantic_context += f" Дополнительные связи (не основная область CORE-2-3/CORE-15): {support_text}."
        conclusion_blocks: list[dict] = []
        for chapter in selected_chapters:
            chapter_id = chapter.get("id")
            match = next((item for item in derived_conclusions if item.get("chapterId") == chapter_id), None)
            if match is None:
                # Fallback: conclusion fragment overlapping the tail of the primary chapter.
                chapter_ids = {block.get("id") for block in chapter.get("blocks", [])}
                match = next((item for item in derived_conclusions if any(block.get("id") in chapter_ids for block in item.get("blocks", []))), None)
            if match is not None:
                conclusion_blocks.extend(match.get("blocks", []))
        if conclusion_blocks:
            fragments.append({
                "id": f"virtual-primary-conclusions-{index + 1}",
                "type": "virtual",
                "selector": "primary_chapter_conclusions",
                "label": f"Выводы основной главы для положения {index + 1}",
                "blocks": _unique_blocks(conclusion_blocks),
                "complete": True,
                "semanticContext": f"Положение {index + 1}: {statement_text}",
            })

        fragments.append({
            "id": f"virtual-defense-chapter-{index + 1}",
            "type": "virtual",
            "selector": "defense_chapter_matrix",
            "label": f"Положение {index + 1} ↔ " + " + ".join(primary_labels),
            "blocks": _unique_blocks(chapter_blocks),
            "complete": bool(
                selected_chapters
                and all(chapter.get("complete") for chapter in selected_chapters)
                and all(item.get("complete") for item in defense)
            ),
            "resultKind": _infer_result_kind(statement_text),
            "semanticContext": semantic_context,
            "chapterIds": [chapter.get("id") for chapter in selected_chapters],
            "chapterRoles": role_rows,
            "mappingScores": [round(score, 3) for _chapter, score, _role in selected_roles],
        })

    for item in derived_conclusions:
        if not item.get("selector"):
            item["selector"] = "chapter_conclusions"

    add_virtual(
        "conclusion_global",
        "Цель, задачи, выводы глав и заключение",
        [*title, *goal, *tasks, *defense, *derived_conclusions, *conclusion],
        [
            *_flatten_blocks(title), *_flatten_blocks(goal), *_flatten_blocks(tasks),
            *_flatten_blocks(defense), *_flatten_blocks(derived_conclusions), *_flatten_blocks(conclusion),
        ],
    )

    implementation_re = re.compile(
        r"внедр\p{L}*|акт\p{L}*\s+внедрен\p{L}*|практическ\p{L}*\s+значим\p{L}*|"
        r"использован\p{L}*\s+результат\p{L}*|по\s+месту\s+работы|"
        r"репозитор\p{L}*|github|gitlab|открыт\p{L}*\s+(?:код|доступ)|"
        r"свидетельств\p{L}*\s+о\s+(?:государственн\p{L}*|гос\p{L}*)\s+рег|"
        r"программн\p{L}*\s+реализац\p{L}*",
        re.I,
    )
    hit_indices = {
        index for index, block in enumerate(blocks)
        if block.get("type") not in {"bibliography", "toc", "figure", "table", "formula"}
        and implementation_re.search(block.get("text", ""))
    }
    context_indices = {
        neighbour
        for index in hit_indices
        for neighbour in range(max(0, index - 1), min(len(blocks), index + 2))
    }
    implementation_blocks = _unique_blocks([
        *_flatten_blocks(introduction),
        *[blocks[index] for index in sorted(context_indices)],
        *_flatten_blocks(conclusion),
    ])
    add_virtual(
        "implementation_context",
        "Внедрение и практическое использование",
        [*introduction, *chapters, *conclusion],
        implementation_blocks,
        True,
        sourceScannedBlocks=len(blocks),
        semanticContext=(
            f"Весь документ ({len(blocks)} блоков) предварительно просканирован по явным маркерам "
            "внедрения, практического использования, программной реализации и явных реквизитов. "
            f"Найдено маркерных блоков: {len(hit_indices)}."
        ),
    )
    return fragments


def _flatten_blocks(fragments: list[dict]) -> list[dict]:
    return [block for fragment in fragments for block in fragment.get("blocks", [])]


def _derive_chapter_conclusion(chapter: dict, index: int) -> dict | None:
    heading = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s*)?выводы(?:\s+по\s+главе)?\.?$", re.I)
    chapter_blocks = chapter.get("blocks", [])
    start = next((i for i, block in enumerate(chapter_blocks) if heading.match(block.get("text", "").strip())), -1)
    if start < 0:
        return None
    return {
        "id": f"{chapter.get('id')}-conclusions",
        "type": "chapter_conclusions",
        "selector": "chapter_conclusions",
        "label": f"Выводы: {chapter.get('label') or f'глава {index + 1}'}",
        "blocks": chapter_blocks[start:],
        "complete": bool(chapter.get("complete")),
    }


def _select_chapter_summary(blocks: list[dict]) -> list[dict]:
    keyword = re.compile(r"предложен|разработан|прототип|аналог|эксперимент|результат|вывод|сравнен|превосход|отлича", re.I)
    selected = [*blocks[:4], *[block for block in blocks if keyword.search(block.get("text", ""))], *blocks[-10:]]
    return _unique_blocks(selected)[:45]


def _select_chapter_evidence(blocks: list[dict]) -> list[dict]:
    keyword = re.compile(r"аналог|прототип|базов(?:ый|ая|ое)|сравнен|недостат|отлича|предложен|результат|эксперимент", re.I)
    selected = [*blocks[:3], *[block for block in blocks if keyword.search(block.get("text", ""))], *blocks[-12:]]
    return _unique_blocks(selected)[:55]


def _split_defense_statements(fragments: list[dict]) -> list[dict]:
    blocks = _unique_blocks(_flatten_blocks(fragments))
    statements: list[dict] = []
    for item in collect_unique_numbered_items(blocks):
        source = dict(item.get("source") or {})
        number = item.get("number")
        source["id"] = f"{source.get('id')}-statement-{number}"
        source["location"] = f"{source.get('location', '')}, положение {number}"
        source["text"] = f"{number}. {item.get('text', '')}"
        statements.append(source)
    return statements



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
    if re.search(r"\b(?:экспериментальн\p{L}*|валидаци\p{L}*|применени\p{L}*)\b", str(chapter.get("label") or ""), re.I):
        score *= 0.78
    return score

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

    if scored and re.search(r"\b(?:экспериментальн\p{L}*|валидаци\p{L}*|применени\p{L}*)\b", str(scored[0][0].get("label") or ""), re.I):
        result_prefix = re.split(r",\s*(?:отлича\p{L}*|характериз\p{L}*|основан\p{L}*)", statement, maxsplit=1, flags=re.I)[0]
        result_sequence = _semantic_sequence(result_prefix)

        def plausible_primary(row: tuple[dict, float]) -> bool:
            chapter, score = row
            if re.search(r"\b(?:экспериментальн\p{L}*|валидаци\p{L}*|применени\p{L}*)\b", str(chapter.get("label") or ""), re.I):
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
        label = str(chapter.get("label") or "")
        chapter_text = _chapter_semantic_text(chapter)
        explicit_position = bool(number and re.search(rf"положени\p{{L}}*\s+{re.escape(number)}\b", chapter_text, re.I))
        is_validation = bool(re.search(r"экспериментальн\p{L}*|валидаци\p{L}*|применени\p{L}*", label, re.I))
        is_implementation = bool(re.search(r"программн\p{L}*\s+(?:инструмент\p{L}*|систем\p{L}*)|реализаци\p{L}*|инструментальн\p{L}*|архитектур\p{L}*", label, re.I))

        if is_validation and (explicit_position or score >= max(0.16, best * 0.38)):
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



def _technical_science_applicability(document: dict) -> bool | None:
    # CORE-14 explicitly applies only to dissertations in technical sciences.
    # Prefer the degree wording on the title pages over inferring the branch from
    # the specialty code, because the same specialty may award different degrees.
    early: list[str] = []
    for index, block in enumerate(document.get("blocks", [])):
        page = block.get("page")
        if page is None:
            if index >= 40:
                break
        else:
            try:
                if int(page) > 6:
                    continue
            except (TypeError, ValueError):
                if index >= 40:
                    continue
        early.append(str(block.get("text") or ""))
    text = re.sub(r"\s+", " ", " ".join(early)).lower().replace("ё", "е")
    match = re.search(r"(?:кандидат(?:а)?|доктор(?:а)?)\s+([^.;:]{2,80}?)\s+наук\b", text, re.I)
    if not match:
        return None
    branch = re.sub(r"\s+", " ", match.group(1)).strip(" —-,:;")
    if "техническ" in branch:
        return True
    return False

def _fallback_selectors(rule: dict) -> list[str]:
    scope = rule.get("scope")
    if scope == "title":
        return ["title"]
    if scope == "goal":
        return ["title_goal"]
    if scope == "defense_statements":
        return ["defense_statements"]
    if scope == "chapter":
        return ["chapter"]
    if scope == "bibliography":
        return ["bibliography"]
    return ["major_sections"]


def _fallback_spec(rule: dict) -> dict:
    if rule.get("mode") == "candidate":
        return {"strategy": "candidate", "candidateFamily": rule.get("candidateFamily"), "exhaustive": True}
    if rule.get("detectorId") or rule.get("mode") == "deterministic":
        return {"strategy": "deterministic"}
    if rule.get("mode") == "structural":
        return {"strategy": "structural"}
    if rule.get("mode") == "manual" or rule.get("scope") in {"presentation", "defense", "process"}:
        return {"strategy": "manual", "reason": "Правило требует другого артефакта или ручного наблюдения."}
    return {
        "strategy": "llm",
        "selectors": _fallback_selectors(rule),
        "exhaustive": rule.get("scope") != "document",
    }


def _expand_selectors(selectors: list[str], fragments: list[dict]) -> list[str]:
    result: list[str] = []
    for selector in selectors:
        if selector == "defense_chapter_matrix":
            result.extend(item["id"] for item in fragments if item.get("selector") == "defense_chapter_matrix")
            continue
        if selector == "major_sections":
            result.extend(item["id"] for item in fragments if item.get("type") in {"introduction", "chapter", "conclusion"})
            continue
        if selector == "chapter_conclusions":
            result.extend(item["id"] for item in fragments if item.get("type") == "chapter_conclusions" or item.get("selector") == "chapter_conclusions")
            continue
        if selector == "primary_chapter_conclusions":
            result.extend(item["id"] for item in fragments if item.get("selector") == "primary_chapter_conclusions")
            continue
        virtual = next((item for item in fragments if item.get("selector") == selector and item.get("type") == "virtual"), None)
        if virtual:
            result.append(virtual["id"])
            continue
        if selector in DIRECT_SELECTORS:
            result.extend(item["id"] for item in fragments if item.get("type") == selector)
    return list(dict.fromkeys(result))


def _route_rule(rule: dict, fragments: list[dict], explicit_spec: dict | None) -> dict:
    spec = dict(explicit_spec or _fallback_spec(rule))
    strategy = spec.get("strategy", _fallback_spec(rule).get("strategy"))

    selectors = spec.get("selectors") or _fallback_selectors(rule)
    fragment_ids = _expand_selectors(selectors, fragments) if strategy == "llm" else []
    by_id = {fragment.get("id"): fragment for fragment in fragments}
    if strategy == "candidate":
        # Candidate collectors scan their complete typed scope in Python. Coverage is
        # calculated later from the actual candidate batches, never from an LLM claim.
        exhaustive = bool(spec.get("exhaustive", True))
    else:
        exhaustive = bool(spec.get("exhaustive")) and bool(fragment_ids) and all(bool(by_id.get(fid, {}).get("complete")) for fid in fragment_ids)

    routed = {
        "rule": rule,
        "strategy": strategy,
        "fragmentIds": fragment_ids,
        "exhaustive": exhaustive,
        "allowPass": spec.get("allowPass") is not False,
        "reason": spec.get("reason"),
        "explicit": explicit_spec is not None,
    }
    if strategy == "candidate":
        routed["candidateFamily"] = spec.get("candidateFamily") or rule.get("candidateFamily")
    # Compatibility extension: if a routing profile explicitly overrides a detector,
    # the checker honours it without changing the public API contract.
    if spec.get("detectorId") or rule.get("detectorId"):
        routed["detectorId"] = spec.get("detectorId") or rule.get("detectorId")
    return routed


async def build_routing(*, document: dict, map_value: dict, rules: list[dict]) -> dict:
    fragments = _build_fragments(document, map_value)
    config = _load_config()
    specs = config.get("rules") or {}
    routed = []
    core14_applicability = _technical_science_applicability(document)
    for rule in rules:
        explicit_spec = specs.get(rule.get("id"))
        if rule.get("id") == "CORE-14" and core14_applicability is False:
            explicit_spec = {
                "strategy": "manual",
                "reason": "CORE-14 относится только к диссертациям по техническим наукам; на титульной странице явно указана иная отрасль науки.",
            }
        routed.append(_route_rule(rule, fragments, explicit_spec))
    explicit_rules = sum(1 for item in routed if item.get("explicit"))
    fallback_rules = len(routed) - explicit_rules
    strategy = "explicit-map" if fallback_rules == 0 else "scope-fallback" if explicit_rules == 0 else "mixed"
    return {
        "fragments": fragments,
        "routed": routed,
        "strategy": strategy,
        "explicitRules": explicit_rules,
        "fallbackRules": fallback_rules,
        "usage": empty_usage(),
    }
