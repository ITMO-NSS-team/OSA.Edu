from __future__ import annotations

import regex as re

from .common import contextual, dedupe_evidence, evidence, mapped_excluded_ids

KNOWN = {
    "LLM": ("abbreviation", True, True),
    "API": ("abbreviation", True, True),
    "MCP": ("protocol", True, True),
    "RAG": ("method_name", True, True),
    "RPS": ("method_name", True, True),
    "NDCG": ("metric", True, True),
    "AC1": ("metric", False, False),
    "GPT": ("model_name", False, False),
    "BERT": ("model_name", False, False),
    "BGE": ("model_name", False, False),
    "GTE": ("model_name", False, False),
    "ORAG": ("method_name", False, False),
    "BM25": ("method_name", False, False),
    "TF–IDF": ("compound_term", False, False),
    "TF-IDF": ("compound_term", False, False),
    "RECALL@10": ("metric", False, False),
    "NDCG@K": ("metric", False, False),
}
IGNORE = {"РФ", "СССР", "ИИ", "ГОСТ", "ВКР", "ГЛАВА", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК", "РЕФЕРАТ", "SYNOPSIS"}
TOKEN_PATTERN = re.compile(r"(?<![\p{L}\p{N}_])(?:TF[–-]IDF|Recall@\d+|NDCG@[A-Z\d]+|GPT(?:-[\w.]+)?|Qwen\d[\w.-]*|MiniLM|[A-ZА-ЯЁ]{2,10}(?:\d+)?)(?![\p{L}\p{N}_])")


def _canonical(term: str) -> str:
    if re.fullmatch(r"Recall@\d+", term, re.I):
        return "Recall@" + term.split("@", 1)[1]
    return term


def _normalize(term: str) -> str:
    return _canonical(term).upper().replace("–", "-")


def _is_roman(term: str) -> bool:
    return bool(re.fullmatch(r"(?:I|II|III|IV|V|VI|VII|VIII|IX|X)", term))


def _classify(term: str) -> tuple[str, bool, bool]:
    direct = KNOWN.get(term) or KNOWN.get(term.upper())
    if direct:
        return direct
    if re.match(r"^(?:Recall|NDCG|F1)@", term, re.I):
        return ("metric", False, False)
    if re.match(r"^(?:GPT|Qwen|MiniLM|BERT|BGE|GTE)", term):
        return ("model_name", False, False)
    return ("unknown", False, False)


def _expansion_at(text: str, index: int, token_length: int) -> dict:
    before = text[max(0, index - 360):index]
    after = text[index + token_length:index + token_length + 50]
    direct = re.search(r"([^.!?;:\n()]{2,220})\(\s*$", before)
    comma = re.search(r"\(\s*([^()]{2,220}),\s*$", before)
    closes = bool(re.match(r"^\s*\)", after))
    phrase = ((comma.group(1) if comma else None) or (direct.group(1) if direct else "")).strip()
    words = [x for x in re.split(r"[\s–—-]+", phrase) if x]
    expanded = closes and len(words) >= 2
    latin_tail = bool(re.search(r"(?:[A-Za-z][A-Za-z-]*\s+){1,}[A-Za-z][A-Za-z-]*$", phrase))
    russian = expanded and bool(re.search(r"[а-яё]", phrase, re.I)) and not latin_tail
    return {"expanded": expanded, "russian": russian, "evidence": phrase if expanded else ""}


def _looks_like_contents(value: str) -> bool:
    compact = re.sub(r"\s+", " ", value)
    return bool(re.search(r"оглавление|содержание", compact, re.I) or (re.search(r"(?:\.\s*){5,}", compact) and re.search(r"\b(?:глава|введение|заключение|раздел)\b", compact, re.I)))


def _is_publication(value: str) -> bool:
    return bool(re.search(r"(?:\bDOI\s*:|//|\bet\s+al\.|Подано\s+на\s+(?:конференцию|рецензирование)|Submitted\s+to|\b(?:AAAI|ACM|EMNLP|GECCO|ICML|NeurIPS)\b[^.!?\n]{0,80}(?:Conference|Workshop|Companion|20\d{2}))", value, re.I))


def _is_code_prompt(value: str) -> bool:
    return bool(re.search(r"(?:Requirements:|Rules:|Generate ONLY|Return ONLY|No commentary|\{[a-z_][^}]*\}|```|^\s*(?:def|class|import|from|SELECT|INSERT|UPDATE)\b)", value, re.I | re.M))


def _is_all_caps_heading(value: str) -> bool:
    compact = re.sub(r"[^А-ЯЁа-яёA-Za-z ]", "", value).strip()
    return 4 <= len(compact) <= 120 and compact == compact.upper() and not bool(re.search(r"[а-яё]", compact))


def _contents_pages(document: dict) -> set[int]:
    blocks = document.get("blocks", [])
    start = next((b.get("page") for b in blocks if re.search(r"(?:^|\n)\s*(?:\d+\s*)?(?:оглавление|содержание)", b.get("text", ""), re.I)), None)
    if start is None:
        return set()
    end = next((b.get("page") for b in blocks if b.get("page") is not None and b.get("page") > start and re.search(r"(?:^|\n)\s*(?:\d+\s*)?(?:реферат|synopsis|введение)", b.get("text", ""), re.I)), None)
    return set(range(int(start), int(end if end is not None else start + 1)))


def _narrative_blocks(document: dict) -> list[dict]:
    excluded = mapped_excluded_ids(document)
    contents_pages = _contents_pages(document)
    result: list[dict] = []
    for block in document.get("blocks", []):
        text = block.get("text", "")
        if block.get("id") in excluded or block.get("type") == "bibliography" or _looks_like_contents(text) or _is_code_prompt(text) or _is_publication(text) or _is_all_caps_heading(text) or block.get("page") in contents_pages:
            continue
        letters = re.findall(r"\p{L}", text)
        cyr = re.findall(r"[А-ЯЁа-яё]", text)
        if letters and len(cyr) / len(letters) >= .28:
            result.append(block)
    return result


def _heading_candidates(document: dict) -> list[tuple[dict, str]]:
    result: list[tuple[dict, str]] = []
    title = document.get("fields", {}).get("title")
    if title:
        result.append((title, title.get("text", "")))
    pattern = re.compile(r"^(?:ГЛАВА\s+\d+|\d+(?:\.\d+)+\.?\s+\p{L}|Введение|Заключение|Список\s+(?:литературы|сокращений)|Приложение\s+\d+)", re.I)
    for block in document.get("blocks", []):
        for line in block.get("text", "").splitlines():
            line = re.sub(r"(?:\.\s*){4,}.*$", "", line).strip()
            if line and pattern.match(line):
                result.append((block, line))
    return result


def analyze_terms(document: dict) -> list[dict]:
    findings: list[dict] = []
    seen: set[str] = set()
    for block in _narrative_blocks(document):
        for match in TOKEN_PATTERN.finditer(block.get("text", "")):
            raw = _canonical(match.group(0))
            key = _normalize(raw)
            if key in seen or raw in IGNORE or _is_roman(raw):
                continue
            seen.add(key)
            kind, requires_expansion, requires_russian = _classify(raw)
            expansion = _expansion_at(block.get("text", ""), match.start(), len(match.group(0)))
            first = evidence(block, contextual(block.get("text", ""), match.start(), len(match.group(0))))
            status = "ok"
            if kind == "unknown":
                status = "review"
            elif requires_expansion and not expansion["expanded"]:
                status = "missing_expansion"
            elif requires_russian and not expansion["russian"]:
                status = "missing_russian_explanation"
            item = {
                "term": raw,
                "kind": kind,
                "firstUse": first,
                "requiresExpansion": requires_expansion,
                "requiresRussianExplanation": requires_russian,
                "status": status,
            }
            if expansion["evidence"]:
                item["expansion"] = evidence(block, expansion["evidence"])
            findings.append(item)
    return findings


def _heading_terms(document: dict) -> list[dict]:
    hits: list[dict] = []
    for block, text in _heading_candidates(document):
        for match in TOKEN_PATTERN.finditer(text):
            term = _canonical(match.group(0))
            if term in IGNORE or _is_roman(term):
                continue
            kind, _, _ = _classify(term)
            if kind == "model_name" and not re.fullmatch(r"(?:LLM|MCP|API)", term):
                continue
            hits.append({"term": term, "evidence": evidence(block, text)})
    return hits


def run_abbreviation_check(rule: dict, document: dict) -> dict:
    rid = rule.get("id")
    if rid in {"CORE-4-2", "SOFT-077"}:
        hits = _heading_terms(document)
        if not hits:
            return _base(rule, "pass", "В названии работы, оглавлении и распознанных заголовках аббревиатуры не обнаружены.")
        terms = list(dict.fromkeys(item["term"] for item in hits))
        return _base(rule, "violation", f"В заголовках обнаружены обозначения: {', '.join(terms)}.", dedupe_evidence([item["evidence"] for item in hits])[:15], "Раскрыть термин в заголовке или заменить сокращение полным названием.", [f"term:{_normalize(term)}:heading" for term in terms])

    findings = analyze_terms(document)
    hard: list[dict] = []
    for item in findings:
        if rid == "CORE-4-3":
            bad = item["requiresRussianExplanation"] and item["status"] in {"missing_russian_explanation", "missing_expansion"}
        elif rid in {"CORE-4-1", "SOFT-056", "SOFT-151"}:
            bad = item["requiresExpansion"] and item["status"] == "missing_expansion"
        else:
            bad = item["status"] in {"missing_expansion", "missing_russian_explanation"}
        if bad:
            hard.append(item)
    heading = _heading_terms(document) if rid == "CORE-12" else []
    ev = dedupe_evidence([item["firstUse"] for item in hard if item.get("firstUse")] + [item["evidence"] for item in heading])[:18]
    if ev:
        terms = list(dict.fromkeys([item["term"] for item in hard] + [item["term"] for item in heading]))
        if rid == "CORE-4-3":
            explanation = f"Иностранные обозначения впервые используются без русского объяснения: {', '.join(terms)}."
        elif rid == "CORE-12":
            explanation = f"Обнаружены необъяснённые обозначения или обозначения в заголовках: {', '.join(terms)}."
        else:
            explanation = f"Обозначения впервые используются без требуемой конструкции «полный термин (обозначение)»: {', '.join(terms)}."
        ids = [f"term:{_normalize(item['term'])}:{'translation' if rid == 'CORE-4-3' else 'expansion'}" for item in hard]
        ids += [f"term:{_normalize(item['term'])}:heading" for item in heading]
        return _base(rule, "violation", explanation, ev, "Для обычных аббревиатур и протоколов дать русский полный термин при первом употреблении; названия моделей, метрик и методов оформлять по их типу, не смешивая их в один список.", list(dict.fromkeys(ids)), findings)
    review = [item for item in findings if item["status"] == "review"]
    if review:
        result = _base(rule, "uncertain", "Найдены обозначения неизвестного или неоднозначного типа: " + ", ".join(item["term"] for item in review[:10]) + ". Они не признаны нарушением автоматически.", [item["firstUse"] for item in review if item.get("firstUse")][:10])
        result["termFindings"] = findings
        return result
    message = "Для распознанных обозначений, требующих русского объяснения, оно найдено." if rid == "CORE-4-3" else "Для распознанных обозначений, требующих раскрытия, первое употребление оформлено корректно."
    result = _base(rule, "pass", message)
    result["termFindings"] = findings
    return result


def combined_abbreviation_rules(rule: dict, document: dict) -> dict:
    first = run_abbreviation_check({**rule, "id": "SOFT-056"}, document)
    headings = run_abbreviation_check({**rule, "id": "SOFT-077"}, document)
    ev = dedupe_evidence([*first.get("evidence", []), *headings.get("evidence", [])])
    if ev:
        return _base(rule, "violation", "Обнаружена аббревиатура без корректной первой расшифровки либо аббревиатура в заголовке/оглавлении.", ev[:15], "При первом употреблении дать полный термин; из заголовков и оглавления аббревиатуры убрать.")
    if first.get("status") == "uncertain":
        return _base(rule, "uncertain", first.get("explanation", "Найдены неоднозначные обозначения."), first.get("evidence", []))
    return _base(rule, "pass", "Первые употребления распознанных аббревиатур раскрыты; в распознанных заголовках аббревиатуры не обнаружены.")


def _base(rule: dict, status: str, explanation: str, ev: list[dict] | None = None, fix: str | None = None, finding_ids: list[str] | None = None, term_findings: list[dict] | None = None) -> dict:
    evidence_items = ev or []
    out = {
        "ruleId": rule.get("id"), "status": status, "severity": rule.get("severity", "major"),
        "explanation": explanation, "confidence": 1 if status in {"pass", "violation"} else 0,
        "evidence": evidence_items, "checkedBy": "detector",
        "evidenceStatus": "verified" if evidence_items else "not_required",
    }
    if fix:
        out["fix"] = fix
    if finding_ids:
        out["findingIds"] = list(dict.fromkeys(finding_ids))
    if term_findings is not None:
        out["termFindings"] = term_findings
    return out
