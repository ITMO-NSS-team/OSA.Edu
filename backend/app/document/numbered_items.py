from __future__ import annotations

import regex as re

DEFENSE_HEADING = re.compile(r'(?:основные\s+)?положения,?\s+выносимые\s+на\s+защиту\.?', re.I)


def collect_numbered_items(blocks: list[dict]) -> list[dict]:
    if not blocks:
        return []
    has_heading = any(DEFENSE_HEADING.search(block.get("text", "").replace("\u00ad", "")) for block in blocks)
    inside = not has_heading
    pieces: list[dict] = []
    for block in blocks:
        text = block.get("text", "").replace("\u00ad", "")
        headings = list(DEFENSE_HEADING.finditer(text))
        if headings:
            last = headings[-1]
            text = text[last.end():]
            inside = True
        elif not inside:
            continue
        text = re.sub(r'^\s*\d{1,3}\s*\n', '', text)
        text = re.sub(r'([А-ЯЁа-яё])-\s*\n\s*([А-ЯЁа-яё])', r'\1\2', text)
        pieces.append({"text": text, "block": block})
    joined = "\n".join(x["text"] for x in pieces)
    starts = list(re.finditer(r'(?:^|\n)\s*(\d{1,3})\.\s+(?=[А-ЯЁA-Z])', joined, re.M))
    if not starts:
        return []
    offsets: list[dict] = []
    cursor = 0
    for piece in pieces:
        offsets.append({"start": cursor, "end": cursor + len(piece["text"]), "block": piece["block"]})
        cursor += len(piece["text"]) + 1
    result: list[dict] = []
    for index, match in enumerate(starts):
        digit_pos = re.search(r'\d', match.group(0))
        start = match.start() + (digit_pos.start() if digit_pos else 0)
        end = starts[index + 1].start() if index + 1 < len(starts) else len(joined)
        raw = joined[start:end].strip()
        source = next((x["block"] for x in offsets if start >= x["start"] and start <= x["end"]), blocks[0])
        text = re.sub(r'^\d+\.\s+', '', raw)
        text = re.sub(r'\s+', ' ', text).strip()
        result.append({"number": int(match.group(1)), "text": text, "body": text, "full": raw, "source": source, "block": source})
    return result


def collect_unique_numbered_items(blocks: list[dict]) -> list[dict]:
    by_number: dict[int, dict] = {}
    for item in collect_numbered_items(blocks):
        current = by_number.get(item["number"])
        if current is None or _quality(item["text"]) > _quality(current["text"]):
            by_number[item["number"]] = item
    return [by_number[key] for key in sorted(by_number)]


def extract_numbered_items(text: str) -> list[dict]:
    # Compatibility helper used by deterministic list checks.
    synthetic = {"id": "synthetic", "location": "", "type": "list", "text": text}
    return collect_numbered_items([synthetic])


def _quality(value: str) -> float:
    words = re.findall(r'[А-ЯЁа-яёA-Za-z]{2,}', value)
    glued_penalty = len(re.findall(r'[а-яё]{18,}', value, re.I)) * 12
    return len(words) * 4 + min(len(value), 1200) / 20 - glued_penalty
