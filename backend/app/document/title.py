from __future__ import annotations

import math
import regex as re

TITLE_HINT = re.compile(r'(?:^|[^\p{L}])(?:модел(?:ь|и|ей)|алгоритм(?:ы|ов)?|метод(?:ы|ов)?|систем(?:а|ы)|технолог(?:ия|ии)|анализ|синтез|управлен(?:ие|ия)|генераци(?:я|и)|сегментаци(?:я|и)|разработк(?:а|и))(?!\p{L})', re.I)
EXCLUDED = re.compile(r'(?:министерств|университет|институт|факультет|кафедр|на\s+правах\s+рукописи|диссертац(?:ия|ии)|на\s+соискание|научн(?:ый|ая)\s+руководител|специальност|санкт-петербург|москва|\b20\d{2}\b)', re.I)
COMMON = {"и", "в", "на", "с", "по", "для", "из", "к", "о", "об", "от", "до", "при", "без", "под", "над", "между", "основе"}


def extract_best_title(range_blocks: list[dict], all_blocks: list[dict] | None = None):
    all_blocks = all_blocks or range_blocks
    lexicon = _build_lexicon(all_blocks)
    candidates: list[tuple[float, int, dict, str]] = []
    for block in range_blocks:
        lines = [_restore_joined_words(_clean_line(line), lexicon) for line in re.split(r'\n+', block.get("text", ""))]
        lines = [line for line in lines if line]
        for start in range(len(lines)):
            if not re.search(r'[А-ЯЁа-яё]', lines[start]) or re.search(r'[A-Za-z]', lines[start]):
                continue
            combined = ""
            for end in range(start, min(len(lines), start + 3)):
                if not re.search(r'[А-ЯЁа-яё]', lines[end]) or re.search(r'[A-Za-z]', lines[end]):
                    break
                combined = re.sub(r'\s+', ' ', f"{combined} {lines[end]}").strip()
                score = _title_score(combined)
                if score > 0:
                    candidates.append((score, len(combined), block, combined))
        whole = _restore_joined_words(_clean_line(re.sub(r'\n+', ' ', block.get("text", ""))), lexicon)
        for variant in _title_variants(whole):
            score = _title_score(variant)
            if score > 0:
                candidates.append((score, len(variant), block, variant))
    if not candidates:
        return None
    _, _, block, text = max(candidates, key=lambda x: (x[0], x[1]))
    return {**block, "text": text}


def _title_variants(value: str) -> list[str]:
    """Return plausible title strings from one PDF text block.

    Digital title pages often place the Russian title and its English translation
    in the same PyMuPDF block. The older extractor rejected the whole block as soon
    as Latin letters appeared, which made the result depend on the structure LLM.
    Keep the original value when it is monolingual and additionally expose the
    Russian prefix when a clear multi-word English translation follows it.
    """
    text = re.sub(r'\s+', ' ', value).strip()
    if not text:
        return []
    variants: list[str] = []
    if not re.search(r'[A-Za-z]', text):
        variants.append(text)
    # Look for the start of a real English phrase, not a single acronym/model name.
    # A tail with at least four Latin words is a strong signal of a bilingual title.
    for match in re.finditer(r'(?<![A-Za-z])(?=[A-Z][A-Za-z-]{2,}(?:\s+(?:[A-Za-z][A-Za-z-]{1,}|of|for|and|on|based)){3,})', text):
        prefix = text[:match.start()].strip(' ;,–—-')
        if len(prefix) >= 25 and re.search(r'[А-ЯЁа-яё]', prefix):
            variants.append(prefix)
            break
    return list(dict.fromkeys(variants))


def _clean_line(value: str) -> str:
    return re.sub(r'\s+', ' ', value.replace('\u00ad', '')).strip()


def _title_score(value: str) -> float:
    text = re.sub(r'^[«"]|[»"]$', '', value).strip()
    if len(text) < 25 or len(text) > 240 or EXCLUDED.search(text) or not TITLE_HINT.search(text):
        return -1
    words = re.findall(r'[\p{L}\p{N}]+(?:-[\p{L}\p{N}]+)*', text)
    if len(words) < 4 or len(words) > 24:
        return -1
    score = min(len(text), 150) + len(words) * 4
    if re.match(r'^(?:модел|алгоритм|метод|систем|технолог|анализ|синтез|управлен|генераци|сегментаци|разработк)', text, re.I):
        score += 55
    if re.search(r'(?:^|[^\p{L}])(?:больших\s+языковых\s+моделей|искусственного\s+интеллекта|машинного\s+обучения)(?!\p{L})', text, re.I):
        score += 15
    if text.endswith('.'):
        score -= 20
    return score


def _build_lexicon(blocks: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for block in blocks:
        normalized = re.sub(r'([А-ЯЁа-яё])\u00ad?\s*\n\s*([А-ЯЁа-яё])', r'\1\2', block.get("text", ""))
        for token in re.findall(r'[А-ЯЁа-яё]{2,30}', normalized):
            lower = token.lower()
            counts[lower] = counts.get(lower, 0) + 1
    for word in COMMON:
        counts[word] = max(20, counts.get(word, 0))
    return counts


def _restore_joined_words(value: str, lexicon: dict[str, int]) -> str:
    parts = re.split(r'(\s+)', value)
    out: list[str] = []
    for part in parts:
        if re.fullmatch(r'\s+', part or '') or not re.fullmatch(r'[А-ЯЁа-яё]{12,}', part or ''):
            out.append(part)
        else:
            out.append(_segment_token(part, lexicon) or part)
    return re.sub(r'\s+', ' ', ''.join(out)).strip()


def _segment_token(token: str, lexicon: dict[str, int]) -> str | None:
    lower = token.lower()
    n = len(lower)
    dp: list[tuple[float, list[str]] | None] = [None] * (n + 1)
    dp[0] = (0.0, [])
    for i in range(n):
        state = dp[i]
        if state is None:
            continue
        score0, words0 = state
        for j in range(i + 1, min(n, i + 30) + 1):
            word = lower[i:j]
            count = lexicon.get(word, 0)
            if not count or (len(word) < 4 and word not in COMMON):
                continue
            length_bonus = min(len(word), 14) * 0.72
            score = score0 + math.log2(count + 1) * 1.2 + length_bonus - 5
            if dp[j] is None or score > dp[j][0]:
                dp[j] = (score, [*words0, word])
    best = dp[n]
    if best is None or len(best[1]) < 2 or any(len(word) == 1 and word not in COMMON for word in best[1]):
        return None
    joined = ' '.join(best[1])
    return joined[:1].upper() + joined[1:] if re.match(r'^[А-ЯЁ]', token) else joined
