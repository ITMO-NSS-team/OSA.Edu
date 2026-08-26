from __future__ import annotations

"""Conservative deterministic abbreviation checks.

Production policy for the repo-stable branch:
- Python owns scope, first-use order and the final CORE-4 verdict.
- Known term abbreviations are checked deterministically.
- Proper names / model versions / units / hardware / publication identifiers are
  excluded before rule evaluation.
- Unknown entities are surfaced as ``uncertain`` rather than guessed into a
  violation.  The legacy LLM abbreviation auditor remains available behind an
  explicit feature flag in orchestration, but it is not the default path.
"""

import regex as re

from .common import (
    contextual,
    dedupe_evidence,
    evidence,
    mapped_excluded_ids,
    narrative_blocks,
    contents_page_range,
    is_code_or_prompt,
)
from ..scope import main_work_ids
from .abbreviation_audit import collect_abbreviation_tokens


# kind, requires CORE-4-1 Russian full-term form, requires CORE-4-3 Russian meaning
KNOWN: dict[str, tuple[str, bool, bool]] = {
    # General technical abbreviations / protocols
    "LLM": ("term_abbreviation", True, True),
    "API": ("term_abbreviation", True, True),
    "MCP": ("protocol_abbreviation", True, True),
    "REST-API": ("protocol_abbreviation", True, True),
    "RAG": ("method_abbreviation", True, True),
    "RPS": ("method_abbreviation", True, True),
    "ANN": ("method_abbreviation", True, True),
    "AST": ("term_abbreviation", True, True),
    "GLR": ("method_abbreviation", True, True),
    "CNN": ("model_abbreviation", True, True),
    "RNN": ("model_abbreviation", True, True),
    "PINN": ("model_abbreviation", True, True),
    "ODE": ("term_abbreviation", True, True),
    "SDE": ("term_abbreviation", True, True),
    "SPDE": ("term_abbreviation", True, True),
    # Methods / models seen in the regression corpus
    "SINDY": ("method_abbreviation", True, True),
    "LASSO": ("method_abbreviation", True, True),
    "STLSQ": ("method_abbreviation", True, True),
    "EPDE": ("method_abbreviation", True, True),
    "ARIMA": ("model_abbreviation", True, True),
    "SARIMA": ("model_abbreviation", True, True),
    "VAR": ("model_abbreviation", True, True),
    "ARCH": ("model_abbreviation", True, True),
    "GARCH": ("model_abbreviation", True, True),
    "LSTM": ("model_abbreviation", True, True),
    "GRU": ("model_abbreviation", True, True),
    "BMA": ("method_abbreviation", True, True),
    "SAGE": ("method_abbreviation", True, True),
    "MNRL": ("method_abbreviation", True, True),
    "UMAP": ("method_abbreviation", True, True),
    "LORA": ("method_abbreviation", True, True),
    # Metrics / formats
    "NDCG": ("metric_abbreviation", True, True),
    "AUC": ("metric_abbreviation", True, True),
    "ROC": ("metric_abbreviation", True, True),
    "RMSE": ("metric_abbreviation", True, True),
    "MAE": ("metric_abbreviation", True, True),
    "MSE": ("metric_abbreviation", True, True),
    "PSNR": ("metric_abbreviation", True, True),
    "SSIM": ("metric_abbreviation", True, True),
    "IOU": ("metric_abbreviation", True, True),
    "MAP": ("metric_abbreviation", True, True),
    "BLEU": ("metric_abbreviation", True, True),
    "ROUGE": ("metric_abbreviation", True, True),
    "SVG": ("term_abbreviation", True, True),
    "XML": ("term_abbreviation", True, True),
    "JSON": ("term_abbreviation", True, True),
    "ASCII": ("term_abbreviation", True, True),
    "CSS": ("term_abbreviation", True, True),
    # Known names / not expansion targets
    "GPT": ("model_name", False, False),
    "BERT": ("model_name", False, False),
    "BGE": ("model_name", False, False),
    "GTE": ("model_name", False, False),
    "ORAG": ("method_name", False, False),
    "BM25": ("method_name", False, False),
    "AC1": ("metric_notation", False, False),
    "TF-IDF": ("compound_term", False, False),
    "DINO": ("model_name", False, False),
    "SAM": ("model_name", False, False),
    "CLIP": ("model_name", False, False),
    "DDPM": ("model_name", False, False),
    "OWL": ("model_name", False, False),
    "GLIP": ("model_name", False, False),
    "DETR": ("model_name", False, False),
    "COCO": ("dataset_name", False, False),
    "MNIST": ("dataset_name", False, False),
    "FAISS": ("library_name", False, False),
}

IGNORE = {
    "РФ", "СССР", "ИИ", "ГОСТ", "ВКР", "ГЛАВА", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ",
    "СПИСОК", "РЕФЕРАТ", "SYNOPSIS", "CONTENT", "CONTENTS", "ABSTRACT",
    "МЕТОД", "МОДЕЛЬ", "АЛГОРИТМ", "СИСТЕМА", "РАЗДЕЛ", "ПОДХОД", "ОЦЕНКА",
    "МЕТРИКА", "ТАБЛИЦА", "РИСУНОК", "ЦЕЛЬ", "ЗАДАЧИ",
}
UNIT_TOKENS = {
    "ГБ", "МБ", "КБ", "ТБ", "ГЦ", "КГЦ", "МГЦ", "ГГЦ", "ММ", "СМ", "КМ",
    "МКМ", "НМ", "МЛ", "МГ", "КГ", "ВТ", "КВТ", "МВТ", "МВ", "МА", "МС",
    "МКС", "НС", "DB", "DBM", "HZ", "KHZ", "MHZ", "GHZ",
}
PUBLICATION_IDENTIFIERS = {"DOI", "ISBN", "ISSN", "ORCID"}
HARDWARE_TOKENS = {"CPU", "GPU", "RAM", "VRAM", "SSD", "HDD", "TPU"}

# Explicit mixed-case technical abbreviations are included without turning every
# CamelCase proper name into a candidate.
TOKEN_PATTERN = re.compile(
    r"(?<![\p{L}\p{N}_@-])(?:"
    r"REST[-–]API|TF[-–]IDF|IoU|mAP|eGFR|SINDy|LoRA|"
    r"Recall@\d+|NDCG@[A-Za-z0-9]+|pass@[A-Za-z0-9]+|F1(?:@\d+)?|R[²2]|"
    r"GPT(?:-[\w.]+)?|Qwen\d[\w.-]*|LLaMA[\w.-]*|Gemma\d?[\w.-]*|MiniLM|"
    r"[A-ZА-ЯЁ]{2,14}(?:[-–/][A-ZА-ЯЁ0-9]{1,14})*(?:\d+)?"
    r")(?![\p{L}\p{N}_@])"
)

ROMAN_RE = re.compile(r"^(?=[MDCLXVI]+$)M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})$")
METRIC_NOTATION_RE = re.compile(r"^(?:F1(?:@\d+)?|R[²2]|Recall@\d+|NDCG@[A-Za-z0-9]+|pass@[A-Za-z0-9]+)$", re.I)
MODEL_VERSION_RE = re.compile(r"^(?:GPT|Qwen|LLaMA|Gemma|Claude|Mistral)[-–]?\d+(?:[.-]\d+)*(?:[A-Za-z.-]+)?$", re.I)


def _canonical(term: str) -> str:
    value = str(term or "").strip().replace("–", "-")
    if re.fullmatch(r"Recall@\d+", value, re.I):
        return "Recall@" + value.split("@", 1)[1]
    if value.lower() == "iou":
        return "IoU"
    if value.lower() == "map":
        return "mAP" if value.startswith("m") else value
    return value


def _normalize(term: str) -> str:
    return _canonical(term).upper().replace("–", "-")


def _is_roman(term: str) -> bool:
    value = _normalize(term)
    return bool(value and ROMAN_RE.fullmatch(value))


def _deterministic_exclusion(term: str) -> str | None:
    key = _normalize(term)
    if not key or key in IGNORE:
        return "ignore"
    if _is_roman(key):
        return "roman_numeral"
    if key in UNIT_TOKENS:
        return "unit"
    if key in PUBLICATION_IDENTIFIERS:
        return "publication_identifier"
    if key in HARDWARE_TOKENS or key == "RTX":
        return "hardware_designation"
    if key in {"HIGH", "MED", "LOW"}:
        return "code_enum"
    if re.fullmatch(r"CWE[-/]\d+|ISO/IEC|CWE/OWASP", key):
        return "standard_identifier"
    if re.fullmatch(r"(?:PSNR|SSIM|RMSE|MAE|MSE|IOU|AUC|ROC)ROI", key):
        return "metric_notation"
    if METRIC_NOTATION_RE.fullmatch(term):
        return "metric_notation"
    if MODEL_VERSION_RE.fullmatch(term):
        return "model_name"
    return None


def _classify(term: str) -> tuple[str, bool, bool]:
    excluded = _deterministic_exclusion(term)
    if excluded:
        return (excluded, False, False)
    key = _normalize(term)
    direct = KNOWN.get(key)
    if direct:
        return direct
    # Model families are names, not abbreviations to be expanded by CORE-4.
    if re.match(r"^(?:GPT|QWEN|MINILM|BERT|BGE|GTE|LLAMA|GEMMA)", key):
        return ("model_name", False, False)
    # Unknown all-caps/mixed technical entities are deliberately not guessed.
    return ("unknown", False, False)


def _term_head_present(value: str) -> bool:
    return bool(re.search(
        r"\b(?:метрик\p{L}*|критери\p{L}*|ошибк\p{L}*|коэффициент\p{L}*|индекс\p{L}*|"
        r"метод\p{L}*|алгоритм\p{L}*|модел\p{L}*|сет\p{L}*|протокол\p{L}*|интерфейс\p{L}*|"
        r"систем\p{L}*|язык\p{L}*|формат\p{L}*|подход\p{L}*|оценк\p{L}*|усреднени\p{L}*|"
        r"распределени\p{L}*|идентификаци\p{L}*|отношени\p{L}*|сходств\p{L}*|"
        r"машин\p{L}*|обучени\p{L}*|адаптац\p{L}*|поиск\p{L}*|генераци\p{L}*|площад\p{L}*)\b",
        value,
        re.I,
    ))


def _russian_parenthetical_term(text: str, token: str) -> str:
    escaped = re.escape(token).replace(r"\-", "[-–]")
    pattern = re.compile(
        rf"(?P<term>[А-ЯЁа-яё][А-ЯЁа-яё\-/]*(?:\s+[«\"]?[А-ЯЁа-яё][А-ЯЁа-яё\-/]*[»\"]?){{1,14}})\s*"
        rf"\([^)]{{0,180}}(?<![\p{{L}}\p{{N}}_]){escaped}(?![\p{{L}}\p{{N}}_])[^)]{{0,180}}\)",
        re.I,
    )
    matches = list(pattern.finditer(text))
    if not matches:
        return ""
    term = matches[-1].group("term").strip()
    return term if _term_head_present(term) else ""


def _russian_bare_term(text: str, token: str) -> str:
    escaped = re.escape(token).replace(r"\-", "[-–]")
    pattern = re.compile(
        rf"(?P<term>[А-ЯЁа-яё][А-ЯЁа-яё\-/]*(?:\s+[А-ЯЁа-яё][А-ЯЁа-яё\-/]*){{1,12}})\s+"
        rf"(?<![\p{{L}}\p{{N}}_]){escaped}(?![\p{{L}}\p{{N}}_])",
        re.I,
    )
    leading_noise = re.compile(
        r"^(?:(?:в|в данной|в настоящей)\s+работе\s+)?(?:используется|использованы|применяется|"
        r"предложен|предложена|предложены|рассматривается|обозначается|служит|является|"
        r"составляет|вводится|введён|получен|разработан|представлен)\s+",
        re.I,
    )
    discourse_noise = re.compile(
        r"^(?:в\s+основе\s+лежит|основу(?:\s+[^,.;:—–]{0,80})?\s+составляет|"
        r"в\s+качестве(?:\s+[^,.;:—–]{0,60})?\s+используется)\s+",
        re.I,
    )
    generic_only = re.compile(
        r"^(?:метод|алгоритм|модель|метрика|показатель|подход|система|протокол|интерфейс|формат|язык)\p{L}*$",
        re.I,
    )
    for match in pattern.finditer(text):
        term = leading_noise.sub("", match.group("term")).strip()
        term = discourse_noise.sub("", term).strip(" —–,:;")
        words = re.findall(r"[А-ЯЁа-яё][А-ЯЁа-яё\-/]*", term)
        if len(words) < 2 or not _term_head_present(term):
            continue
        # A bare generic label like «алгоритм SINDy» or «метрика AUC» is not a
        # Russian translation.  At least one descriptive Russian word must remain.
        descriptive = [word for word in words if not generic_only.fullmatch(word)]
        if not descriptive:
            continue
        return term
    return ""


def _english_with_russian_translation_after(text: str, token: str) -> bool:
    escaped = re.escape(token).replace(r"\-", "[-–]")
    return bool(re.search(
        rf"(?<![\p{{L}}\p{{N}}_]){escaped}(?![\p{{L}}\p{{N}}_])\s*\("
        rf"[^)]{{0,220}}(?:—|–|;)\s*[А-ЯЁа-яё][^)]{{2,180}}\)",
        text,
        re.I,
    ))


def _term_context(text: str, index: int, length: int) -> str:
    return text[max(0, index - 420): min(len(text), index + length + 260)]


def _introduction_state_blocks(document: dict) -> list[dict]:
    """Authored narrative that can establish the first meaningful use.

    Publication/approbation metadata inside the introduction is intentionally
    skipped until the next structural boundary.  This avoids treating conference
    acronyms and citation metadata as the first use of a scientific term.
    """
    base = narrative_blocks(document)
    out: list[dict] = []
    skip: str | None = None
    start = re.compile(r"^(?:Апробация(?: результатов)?(?: работы)?|Публикации(?: автора)?(?: по теме диссертации)?|Publications)\b", re.I)
    structure_end = re.compile(r"^(?:Структура и (?:объ[eё]м|объем)|Structure of the (?:thesis|dissertation))\b", re.I)
    generic_end = re.compile(r"^(?:Личный вклад автора|Внедрение результатов|Структура и (?:объ[eё]м|объем)|ГЛАВА\s+\d+)\b", re.I)
    for block in base:
        text = str(block.get("text") or "")
        compact = " ".join(text.split())
        if skip == "publications" and structure_end.match(compact):
            skip = None
        elif skip == "approbation" and generic_end.match(compact):
            skip = None
        elif skip and block.get("type") == "heading" and not start.match(compact):
            skip = None
        if start.match(compact):
            skip = "publications" if re.match(r"^(?:Публикации|Publications)", compact, re.I) else "approbation"
            continue
        if skip:
            continue
        if _is_publication(text) or _looks_like_publication_metadata(text) or _looks_like_pseudocode(text) or _looks_like_table(text, block):
            continue
        out.append(block)
    return out


def _looks_like_contents(value: str) -> bool:
    compact = re.sub(r"\s+", " ", value)
    return bool(re.search(r"оглавление|содержание", compact, re.I) or (re.search(r"(?:\.\s*){5,}", compact) and re.search(r"\b(?:глава|введение|заключение|раздел)\b", compact, re.I)))


def _is_publication(value: str) -> bool:
    return bool(re.search(
        r"(?:\bDOI\s*:|//|\bet\s+al\.|Подано\s+на\s+(?:конференцию|рецензирование)|Submitted\s+to|"
        r"\b(?:AAAI|ACM|EMNLP|GECCO|ICML|NeurIPS)\b[^.!?\n]{0,100}(?:Conference|Workshop|Companion|20\d{2}))",
        value,
        re.I,
    ))


def _looks_like_publication_metadata(value: str) -> bool:
    text = " ".join(str(value or "").split())
    if re.match(r"^\d{1,2}\.\s+", text) and ("//" in text or re.search(r"\b(?:DOI|Scopus|Web of Science|Workshop|Conference)\b", text, re.I)):
        return True
    return bool(re.match(r"^(?:Основные результаты .{0,100} (?:изложены|опубликованы)|В совместных публикациях)\b", text, re.I))


def _looks_like_pseudocode(value: str) -> bool:
    text = " ".join(str(value or "").split())
    if is_code_or_prompt(text):
        return True
    if re.search(r"#\s*Here are some relevant code fragments|%[A-Z][A-Z _-]{3,}%|continue the function:\s*def\b", text, re.I):
        return True
    markers = 0
    markers += 1 if re.search(r"(?:←|:=|->)", text) else 0
    markers += 1 if "//" in text else 0
    markers += 1 if len(re.findall(r"\b[A-Za-z][A-Za-z0-9_]*\s*\([^)]{0,100}\)", text)) >= 2 else 0
    markers += 1 if len(re.findall(r"(?:^|[;:.])\s*\d{1,3}\s+(?:Этап\s+\d+|for\b|while\b|if\b|return\b)", text, re.I)) >= 2 else 0
    return markers >= 2


def _looks_like_table(value: str, block: dict | None = None) -> bool:
    if block and str(block.get("type") or "").lower() == "table":
        return True
    text = " ".join(str(value or "").split())
    if not text:
        return False
    numbers = len(re.findall(r"(?<!\p{L})\d+(?:[,.]\d+)?", text))
    acronyms = len(re.findall(r"(?<!\w)[A-ZА-ЯЁ]{2,10}(?!\w)", text))
    parens = len(re.findall(r"\([^)]{2,60}\)", text))
    sentences = len(re.findall(r"[.!?](?:\s|$)", text))
    header = bool(re.match(r"^(?:Метрика|Модель|Датасет|Гиперпараметр|Параметр|Конфигурация|Кодировщик|Уравнение|Metric|Model|Dataset)\b", text, re.I))
    column_pair = bool(re.match(r"^(?:Модель\s+Робот|Уравнение\s+Метрика|Датасет\s+Модель|Параметр\s+Значение)\b", text, re.I))
    embedded_metric_table = bool(re.search(r"\bМетрика\s+(?:Python|Java|Go|JS|Среднее)\b", text[:360], re.I))
    if column_pair and acronyms >= 2 and len(text) <= 180:
        return True
    if embedded_metric_table and numbers >= 6:
        return True
    if header and (numbers >= 2 or acronyms >= 3):
        return True
    if len(text) <= 700 and numbers >= 6 and acronyms >= 3 and sentences <= 1:
        return True
    if len(text) <= 500 and acronyms >= 5 and parens >= 3 and sentences <= 1:
        return True
    return False


def _contents_pages(document: dict) -> set[int]:
    return contents_page_range(document)


def _heading_candidates(document: dict) -> list[tuple[dict, str]]:
    """Only title, real TOC entries and plausible real section headings."""
    result: list[tuple[dict, str]] = []
    seen: set[tuple[str, str]] = set()
    title = document.get("fields", {}).get("title")
    if title:
        result.append((title, str(title.get("text") or "")))
    contents_pages = _contents_pages(document)
    main_ids = main_work_ids(document)
    pattern = re.compile(r"^(?:ГЛАВА\s+\d+|\d+(?:\.\d+)+\.?\s+\p{L}|Введение|Заключение|Список\s+(?:литературы|сокращений)|Приложение\s+\d+)", re.I)
    for block in document.get("blocks", []):
        text = str(block.get("text") or "")
        if _looks_like_table(text, block) or _looks_like_pseudocode(text):
            continue
        in_toc = block.get("page") in contents_pages
        in_main = main_ids is None or str(block.get("id")) in main_ids
        is_heading_block = str(block.get("type") or "").lower() == "heading"
        if not in_toc and not (in_main and is_heading_block):
            # Conservative fallback for extractors that lose heading type: accept
            # only a short, single-line, explicitly numbered heading.
            compact = " ".join(text.split())
            if not (in_main and len(compact) <= 220 and "\n" not in text and pattern.match(compact)):
                continue
        accept_heading_line = in_main and is_heading_block
        for line in text.splitlines() or [text]:
            line = re.sub(r"(?:\.\s*){4,}.*$", "", line).strip()
            if not line:
                continue
            if not accept_heading_line and not pattern.match(line):
                continue
            key = (str(block.get("id")), line)
            if key not in seen:
                seen.add(key)
                result.append((block, line))
    return result


def _coverage(findings: list[dict], *, extra_total: int = 0, extra_checked: int = 0) -> dict:
    total = len(findings) + extra_total
    classified = sum(1 for item in findings if item.get("kind") != "unknown") + extra_checked
    return {
        "domain": "abbreviation_candidates",
        "candidateCount": total,
        "checkedCandidateCount": classified,
        "respondedCandidateCount": total,
        "terminalCandidateCount": classified,
        "ambiguousCandidateCount": max(0, total - classified),
        "exhaustive": classified == total,
    }


def analyze_terms(document: dict) -> list[dict]:
    findings: list[dict] = []
    seen: set[str] = set()
    for block in _introduction_state_blocks(document):
        text = str(block.get("text") or "")
        for match in TOKEN_PATTERN.finditer(text):
            raw = _canonical(match.group(0))
            key = _normalize(raw)
            if key in seen or _deterministic_exclusion(raw):
                continue
            seen.add(key)
            kind, requires_expansion, requires_russian = _classify(raw)
            local = _term_context(text, match.start(), len(match.group(0)))
            russian_parenthetical = _russian_parenthetical_term(local, raw)
            russian_bare = _russian_bare_term(local, raw)
            russian_translation = bool(russian_parenthetical or russian_bare or _english_with_russian_translation_after(local, raw))
            status = "ok"
            if kind == "unknown":
                status = "review"
            elif requires_expansion and not russian_parenthetical:
                status = "missing_expansion"
            elif requires_russian and not russian_translation:
                status = "missing_russian_explanation"
            first = evidence(block, contextual(text, match.start(), len(match.group(0)), before=180, after=260))
            first["token"] = raw
            first["entityKind"] = kind
            item = {
                "term": raw,
                "kind": kind,
                "firstUse": first,
                "requiresExpansion": requires_expansion,
                "requiresRussianExplanation": requires_russian,
                "russianFullTerm": russian_parenthetical,
                "russianTranslation": russian_translation,
                "status": status,
            }
            findings.append(item)
    return findings


def _heading_terms(document: dict) -> list[dict]:
    hits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for block, text in _heading_candidates(document):
        for match in TOKEN_PATTERN.finditer(text):
            term = _canonical(match.group(0))
            if _deterministic_exclusion(term):
                continue
            kind, requires_expansion, _ = _classify(term)
            key = (_normalize(term), str(block.get("id")))
            if key in seen:
                continue
            seen.add(key)
            ev = evidence(block, text)
            ev["token"] = term
            ev["entityKind"] = kind
            hits.append({
                "term": term,
                "kind": kind,
                "requiresExpansion": requires_expansion,
                "status": "review" if kind == "unknown" else ("violation" if requires_expansion else "ok"),
                "evidence": ev,
            })
    return hits


def run_abbreviation_check(rule: dict, document: dict) -> dict:
    rid = str(rule.get("id") or "")
    if rid in {"CORE-4-2", "SOFT-077"}:
        hits = _heading_terms(document)
        hard = [item for item in hits if item["status"] == "violation"]
        review = [item for item in hits if item["status"] == "review"]
        coverage = _coverage([], extra_total=len(hits), extra_checked=len(hits) - len(review))
        if hard:
            terms = list(dict.fromkeys(item["term"] for item in hard))
            result = _base(
                rule,
                "violation",
                f"В названии работы, оглавлении или реальных заголовках обнаружены аббревиатуры: {', '.join(terms)}.",
                dedupe_evidence([item["evidence"] for item in hard])[:18],
                "Использовать полный термин в заголовке; не требуется придумывать новое русское сокращение.",
                [f"term:{_normalize(term)}:heading" for term in terms],
            )
            result["coverage"] = coverage
            result["manualReviewCount"] = len(review)
            return result
        if review:
            result = _base(
                rule,
                "uncertain",
                "В заголовках найдены обозначения неизвестного типа: " + ", ".join(item["term"] for item in review[:12]) + ". Они не признаны нарушением автоматически.",
                [item["evidence"] for item in review[:12]],
            )
            result["coverage"] = coverage
            result["manualReviewCount"] = len(review)
            return result
        result = _base(rule, "pass", "В названии работы, оглавлении и распознанных заголовках запрещённые аббревиатуры не обнаружены.")
        result["coverage"] = coverage
        return result

    findings = analyze_terms(document)
    hard: list[dict] = []
    for item in findings:
        if rid == "CORE-4-3":
            bad = item["requiresRussianExplanation"] and not item.get("russianTranslation")
        elif rid in {"CORE-4-1", "SOFT-056", "SOFT-151"}:
            bad = item["requiresExpansion"] and not item.get("russianFullTerm")
        else:
            bad = item["requiresExpansion"] and not item.get("russianFullTerm")
        if bad:
            hard.append(item)

    heading = _heading_terms(document) if rid == "CORE-12" else []
    heading_hard = [item for item in heading if item["status"] == "violation"]
    review = [item for item in findings if item["status"] == "review"]
    heading_review = [item for item in heading if item["status"] == "review"]
    ev = dedupe_evidence(
        [item["firstUse"] for item in hard if item.get("firstUse")]
        + [item["evidence"] for item in heading_hard]
    )[:18]
    coverage = _coverage(findings, extra_total=len(heading) if rid == "CORE-12" else 0, extra_checked=(len(heading) - len(heading_review)) if rid == "CORE-12" else 0)

    if ev:
        terms = list(dict.fromkeys([item["term"] for item in hard] + [item["term"] for item in heading_hard]))
        if rid == "CORE-4-3":
            explanation = f"Иностранные аббревиатуры используются без подтверждённого русского полного термина/перевода: {', '.join(terms)}."
            fix = "Добавить русский полный термин/перевод; исходную иностранную аббревиатуру можно сохранить. Новая русская аббревиатура не требуется."
        elif rid == "CORE-12":
            explanation = f"Обнаружены необъяснённые аббревиатуры или аббревиатуры в заголовках: {', '.join(terms)}."
            fix = "При первом содержательном употреблении указать русский полный термин и исходную аббревиатуру в скобках; из заголовков сокращения убрать."
        else:
            explanation = f"Первое содержательное употребление не оформлено по схеме «полный русский термин (аббревиатура)»: {', '.join(terms)}."
            fix = "Указать полный русский термин, затем исходную аббревиатуру в скобках. Не создавать новое русское сокращение."
        ids = [f"term:{_normalize(item['term'])}:{'translation' if rid == 'CORE-4-3' else 'expansion'}" for item in hard]
        ids += [f"term:{_normalize(item['term'])}:heading" for item in heading_hard]
        result = _base(rule, "violation", explanation, ev, fix, list(dict.fromkeys(ids)), findings)
        result["coverage"] = coverage
        result["manualReviewCount"] = len(review) + len(heading_review)
        return result

    all_review = review + heading_review
    if all_review:
        ev_review = [item.get("firstUse") for item in review if item.get("firstUse")] + [item["evidence"] for item in heading_review]
        result = _base(
            rule,
            "uncertain",
            "Найдены обозначения неизвестного или неоднозначного типа: " + ", ".join(item["term"] for item in all_review[:12]) + ". Они не признаны нарушением автоматически.",
            ev_review[:12],
        )
        result["coverage"] = coverage
        result["termFindings"] = findings
        result["manualReviewCount"] = len(all_review)
        return result

    message = "Для распознанных иностранных аббревиатур русский смысл подтверждён." if rid == "CORE-4-3" else "Для распознанных аббревиатур первое содержательное употребление оформлено корректно."
    result = _base(rule, "pass", message)
    result["coverage"] = coverage
    result["termFindings"] = findings
    return result



def build_llm_abbreviation_inventory(document: dict) -> list[dict]:
    """Build a high-recall compact inventory for the LLM abbreviation judge.

    3.9.3-rc2 deliberately does not call ``analyze_terms`` here: that function is
    a conservative deterministic classifier and therefore can suppress unknown
    domain-specific candidates before the LLM sees them.  The rc2 inventory is
    instead produced by ``collect_abbreviation_tokens`` which scans the canonical
    main work with broad lexical rules and keeps role-labelled contexts.
    """
    raw_items = collect_abbreviation_tokens(document)
    result: list[dict] = []
    for index, raw in enumerate(raw_items, start=1):
        term = str(raw.get("token") or "").strip()
        if not term:
            continue
        result.append({
            "candidateId": f"abbr-{index:04d}",
            "term": term,
            "firstUse": dict(raw.get("firstUse") or {}) or None,
            "headingUses": [dict(ev) for ev in raw.get("headingUses") or []][:2],
            "contextUses": [dict(ev) for ev in raw.get("contextUses") or []][:2],
            "contentRoles": list(raw.get("roles") or []),
            "occurrenceCount": int(raw.get("occurrenceCount") or 0),
            "contextLanguage": str(raw.get("contextLanguage") or "unknown"),
            "listedDefinitions": [dict(ev) for ev in raw.get("listedDefinitions") or []][:3],
        })
    return result

def combined_abbreviation_rules(rule: dict, document: dict) -> dict:
    first = run_abbreviation_check({**rule, "id": "SOFT-056"}, document)
    headings = run_abbreviation_check({**rule, "id": "SOFT-077"}, document)
    ev = dedupe_evidence([*first.get("evidence", []), *headings.get("evidence", [])])
    if ev:
        return _base(rule, "violation", "Обнаружена аббревиатура без корректной первой расшифровки либо аббревиатура в заголовке/оглавлении.", ev[:15], "При первом употреблении дать полный термин; из заголовков и оглавления аббревиатуры убрать.")
    if first.get("status") == "uncertain" or headings.get("status") == "uncertain":
        return _base(rule, "uncertain", "Найдены обозначения, тип которых требует ручной проверки.", dedupe_evidence([*first.get("evidence", []), *headings.get("evidence", [])])[:12])
    return _base(rule, "pass", "Первые употребления распознанных аббревиатур раскрыты; в распознанных заголовках аббревиатуры не обнаружены.")


def _base(
    rule: dict,
    status: str,
    explanation: str,
    ev: list[dict] | None = None,
    fix: str | None = None,
    finding_ids: list[str] | None = None,
    term_findings: list[dict] | None = None,
) -> dict:
    evidence_items = ev or []
    out = {
        "ruleId": rule.get("id"),
        "status": status,
        "severity": rule.get("severity", "major"),
        "explanation": explanation,
        "confidence": 1 if status in {"pass", "violation"} else 0,
        "evidence": evidence_items,
        "checkedBy": "detector",
        "evidenceStatus": "verified" if evidence_items else "not_required",
    }
    if fix:
        out["fix"] = fix
    if finding_ids:
        out["findingIds"] = list(dict.fromkeys(finding_ids))
    if term_findings is not None:
        out["termFindings"] = term_findings
    return out
