from __future__ import annotations

import regex as re


def trim_blocks_for_element(element_type: str, blocks: list[dict]) -> list[dict]:
    if not blocks:
        return []
    if element_type == "tasks":
        return _trim_between(blocks, re.compile(r'(?:^|\n|[.!?]\s+)Задачи\s+(?:диссертационной\s+)?работы\s*\.?', re.I), [
            re.compile(r'(?:^|\n|[.!?]\s+)(?:Объект\s+и\s+предмет\s+исследования|Научная\s+новизна|Теоретическая\s+и\s+практическая\s+значимость|Положения,?\s+выносимые\s+на\s+защиту|На\s+защиту\s+выносятся(?:\s+(?:следующие\s+)?положения)?)\s*[:.]?', re.I)
        ])
    if element_type == "defense_statements":
        return _trim_between(blocks, re.compile(
            r'(?:^|\n|[.!?]\s+)(?:Положения,?\s+выносимые\s+на\s+защиту|На\s+защиту\s+выносятся(?:\s+(?:следующие\s+)?положения)?)\s*[:.]?',
            re.I,
        ), [
            re.compile(r'(?:^|\n|[.!?]\s+)(?:Научная\s+новизна(?:\s+работы)?|Достоверность(?:\s+научных\s+результатов)?|Степень\s+достоверности|Апробация\s+работы|Личный\s+вклад\s+автора|Методологическ\p{L}*\s+основ\p{L}*\s+работы|Практическая\s+значимость(?:\s+работы)?|Теоретическая\s+значимость(?:\s+работы)?|Публикации|Структура\s+и\s+объ[её]м)\s*\.?', re.I)
        ])
    if element_type == "goal":
        return _trim_between(blocks, re.compile(r'(?:^|\n|[.!?]\s+)Цель\s+(?:диссертационной\s+)?работы\s*\.?', re.I), [
            re.compile(r'(?:^|\n|[.!?]\s+)Задачи\s+(?:диссертационной\s+)?работы\s*\.?', re.I)
        ])
    if element_type == "bibliography":
        return _trim_between(blocks, re.compile(r'(?:^|\n)Список\s+(?:использованных\s+)?(?:источников|литературы)\s*\.?', re.I), [
            re.compile(r'(?:^|\n)\s*(?:\d+(?:\.\d+)*\.?\s*)?(?:Приложение|Appendix)\b', re.I)
        ])
    return blocks


def _trim_between(blocks: list[dict], start_pattern, end_patterns: list) -> list[dict]:
    start_block = 0
    start_offset = 0
    found_start = False
    for index, block in enumerate(blocks):
        match = start_pattern.search(block.get("text", ""))
        if not match:
            continue
        start_block = index
        start_offset = match.start() + _leading_delimiter_length(match.group(0))
        found_start = True
        break
    if not found_start:
        return blocks
    end_block = len(blocks) - 1
    end_offset = len(blocks[end_block].get("text", ""))
    stop = False
    for index in range(start_block, len(blocks)):
        if stop:
            break
        from_offset = start_offset + 1 if index == start_block else 0
        tail = blocks[index].get("text", "")[from_offset:]
        for pattern in end_patterns:
            match = pattern.search(tail)
            if not match:
                continue
            end_block = index
            end_offset = from_offset + match.start() + _leading_delimiter_length(match.group(0))
            stop = True
            break
    result: list[dict] = []
    for index in range(start_block, end_block + 1):
        text = blocks[index].get("text", "")
        if index == start_block:
            text = text[start_offset:]
        if index == end_block:
            text = text[:max(0, end_offset - start_offset) if index == start_block else end_offset]
        text = text.strip()
        if text:
            result.append({**blocks[index], "text": text})
    return result or blocks


def _leading_delimiter_length(value: str) -> int:
    match = re.match(r'^(?:\n|[.!?]\s+)', value)
    return len(match.group(0)) if match else 0
