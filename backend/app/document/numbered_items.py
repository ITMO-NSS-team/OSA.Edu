from __future__ import annotations

import regex as re

from .section_signals import find_defense_heading_span

# Numbered lists in theses are not uniform: 1., 1), and (1) all occur in
# practice. Keep the generic collector numeric-only so ordinary list checks do
# not suddenly treat every dash in prose as a numbered item.
_NUMBERED_START = re.compile(r'(?:^|\n)\s*(?:\((\d{1,3})\)|(\d{1,3})[.)])\s+', re.M)
_BULLET_START = re.compile(r'(?:^|\n)\s*([–—•-])\s+', re.M)

_SECTION_TERMINATOR = re.compile(
    r'(?<=[.!?])\s+(?='
    r'(?:Научная\s+новизна(?:\s+работы)?|'
    r'Практическая\s+значимость(?:\s+(?:работы|результатов))?|'
    r'Теоретическая\s+значимость(?:\s+(?:работы|результатов))?|'
    r'Личный\s+вклад(?:\s+автора)?|'
    r'Методологическ\p{L}*\s+основ\p{L}*\s+работы|'
    r'Апробация(?:\s+работы)?|'
    r'Публикации(?:\s+по\s+теме(?:\s+(?:работы|диссертации))?)?|'
    r'Структура\s+(?:дипломной\s+)?работы|'
    r'Работа\s+состоит|'
    r'Использование\s+(?:AI|ИИ|систем\s+искусственного\s+интеллекта))\b'
    r')',
    re.I,
)


def collect_numbered_items(blocks: list[dict]) -> list[dict]:
    return _collect_items(blocks, allow_bullets=False)


def collect_defense_items(blocks: list[dict]) -> list[dict]:
    """Collect complete defense statements from a mapped defense section.

    Defense statements are commonly formatted as ``1.``, ``1)``, ``(1)`` or
    as a simple dash/bullet list.  Bullet items are numbered by their document
    order only when no explicit numeric markers are present.  This fallback is
    deliberately scoped to the already-mapped defense section.
    """
    return _collect_items(blocks, allow_bullets=True)


def _collect_items(blocks: list[dict], *, allow_bullets: bool) -> list[dict]:
    if not blocks:
        return []

    has_heading = any(find_defense_heading_span(_clean(block.get("text", ""))) for block in blocks)
    inside = not has_heading
    pieces: list[dict] = []
    for block in blocks:
        text = _clean(block.get("text", ""))
        heading_span = find_defense_heading_span(text)
        if heading_span:
            text = text[heading_span[1]:]
            inside = True
        elif not inside:
            continue
        # Some PDF layouts put a detached item number on its own first line.
        text = re.sub(r'^\s*\d{1,3}\s*\n', '', text)
        text = re.sub(r'([А-ЯЁа-яё])-\s*\n\s*([А-ЯЁа-яё])', r'\1\2', text)
        pieces.append({"text": text, "block": block})

    joined = "\n".join(piece["text"] for piece in pieces)
    numbered = list(_NUMBERED_START.finditer(joined))
    if numbered:
        starts = [
            {
                "match": match,
                "number": int(match.group(1) or match.group(2)),
                "marker_end": match.end(),
            }
            for match in numbered
        ]
        if allow_bullets:
            starts = _best_defense_number_run(starts)
    elif allow_bullets:
        bullets = list(_BULLET_START.finditer(joined))
        starts = [
            {"match": match, "number": index + 1, "marker_end": match.end()}
            for index, match in enumerate(bullets)
        ]
    else:
        starts = []

    if not starts:
        return []

    offsets: list[dict] = []
    cursor = 0
    for piece in pieces:
        offsets.append({"start": cursor, "end": cursor + len(piece["text"]), "block": piece["block"]})
        cursor += len(piece["text"]) + 1

    result: list[dict] = []
    for index, row in enumerate(starts):
        match = row["match"]
        # Preserve the full marker in ``full`` for evidence, but ``text`` starts
        # after it.  ``match.start`` can point to the preceding newline.
        marker_start = match.start()
        while marker_start < match.end() and joined[marker_start].isspace():
            marker_start += 1
        end = starts[index + 1]["match"].start() if index + 1 < len(starts) else len(joined)
        raw = joined[marker_start:end].strip()
        body_start = row["marker_end"]
        text = re.sub(r'\s+', ' ', joined[body_start:end]).strip()
        text = _trim_after_next_section(text)
        if not text:
            continue
        source = next(
            (item["block"] for item in offsets if marker_start >= item["start"] and marker_start <= item["end"]),
            blocks[0],
        )
        result.append({
            "number": row["number"],
            "text": text,
            "body": text,
            "full": raw if text in raw else text,
            "source": source,
            "block": source,
        })
    return result


def _best_defense_number_run(starts: list[dict]) -> list[dict]:
    """Choose the longest contiguous 1..N run inside a mapped defense range.

    Structure mapping can be off by one block.  A common case is the final item
    of scientific novelty (e.g. ``5) ...``) followed by defense statements that
    restart at ``1)``.  Taking the longest 1..N run is deterministic and avoids
    letting the stray earlier item replace the real last position.
    """
    ones = [i for i, row in enumerate(starts) if row.get("number") == 1]
    if not ones:
        return starts
    runs: list[list[dict]] = []
    for begin in ones:
        run: list[dict] = []
        expected = 1
        for row in starts[begin:]:
            number = int(row.get("number") or 0)
            if number != expected:
                break
            run.append(row)
            expected += 1
        if run:
            runs.append(run)
    if not runs:
        return starts
    return max(runs, key=lambda run: (len(run), -starts.index(run[0])))


def collect_unique_numbered_items(blocks: list[dict]) -> list[dict]:
    return _unique_by_number(collect_numbered_items(blocks))


def collect_unique_defense_items(blocks: list[dict]) -> list[dict]:
    return _unique_by_number(collect_defense_items(blocks))


def _unique_by_number(items: list[dict]) -> list[dict]:
    by_number: dict[int, dict] = {}
    for item in items:
        current = by_number.get(item["number"])
        if current is None or _quality(item["text"]) > _quality(current["text"]):
            by_number[item["number"]] = item
    return [by_number[key] for key in sorted(by_number)]


def extract_numbered_items(text: str) -> list[dict]:
    # Compatibility helper used by deterministic ordinary-list checks.
    synthetic = {"id": "synthetic", "location": "", "type": "list", "text": text}
    return collect_numbered_items([synthetic])


def _trim_after_next_section(value: str) -> str:
    match = _SECTION_TERMINATOR.search(value)
    return value[:match.start()].strip() if match else value.strip()


def _clean(value: str) -> str:
    return str(value or "").replace("\u00ad", "")


def _quality(value: str) -> float:
    words = re.findall(r'[А-ЯЁа-яёA-Za-z]{2,}', value)
    glued_penalty = len(re.findall(r'[а-яё]{18,}', value, re.I)) * 12
    return len(words) * 4 + min(len(value), 1200) / 20 - glued_penalty
