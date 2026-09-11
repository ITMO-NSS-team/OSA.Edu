from __future__ import annotations

import html
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_RE = re.compile(r"(?:arXiv\s*:\s*|arxiv\.org/(?:abs|pdf)/|\babs/)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)", re.I)
URL_RE = re.compile(r"https?://[^\s<>\]\[\"']+", re.I)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
PAGES_RE = re.compile(r"(?:\b(?:p|pp|pages?)\.?\s*|\b(?:с|стр)\.?\s*)(\d+)\s*[–—-]\s*(\d+)", re.I)

SOURCE_TYPES = {
    "PAPER",
    "PREPRINT",
    "BOOK",
    "STANDARD",
    "REPORT",
    "DATASET",
    "DOCUMENTATION",
    "REPOSITORY",
    "WEB",
    "OTHER",
    "UNKNOWN",
}


def normalize_text(value: str) -> str:
    value = html.unescape(value or "")
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("ё", "е")
    value = re.sub(r"[^\w\s]", " ", value, flags=re.U)
    return re.sub(r"\s+", " ", value).strip()


def extract_url(reference: str) -> str:
    match = URL_RE.search(reference or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,;:)]}>")


def extract_doi(reference: str) -> str:
    match = DOI_RE.search(reference or "")
    if not match:
        return ""
    return match.group(0).rstrip(".,;)").lower()


def extract_arxiv_id(reference: str) -> str:
    match = ARXIV_RE.search(reference or "")
    if not match:
        return ""
    return match.group(1).lower()


def extract_years(reference: str) -> list[str]:
    values: list[str] = []
    for year in YEAR_RE.findall(reference or ""):
        if year not in values:
            values.append(year)
    return values


def extract_publication_years(reference: str) -> list[str]:
    """Extract bibliographic years without treating an access date as publication metadata."""
    text = reference or ""
    # GOST/web citations often append `(дата обращения: 16.12.2025)` or an
    # English `accessed/retrieved ...` field.  Its year must not mask a wrong
    # publication year during metadata comparison.
    text = re.sub(
        r"\(?\s*(?:дата\s+обращения|accessed|retrieved|access\s+date)\s*[:,-]?[^)]*(?:\)|$)",
        " ",
        text,
        flags=re.I,
    )
    return extract_years(text)


def extract_pages(reference: str) -> str:
    match = PAGES_RE.search(reference or "")
    return f"{match.group(1)}-{match.group(2)}" if match else ""


def _host(reference: str) -> str:
    url = extract_url(reference)
    if not url:
        return ""
    try:
        return (urlparse(url).hostname or "").casefold()
    except Exception:
        return ""


def classify_source_type(reference: str, legacy_kind: str = "") -> str:
    """Classify what the cited source *is*, independently from verification verdict."""
    ref = reference or ""
    low = normalize_text(ref)
    host = _host(ref)

    if extract_arxiv_id(ref) or "arxiv preprint" in low or re.search(r"\bcorr\s+abs\b", low):
        return "PREPRINT"
    if re.search(r"\b(?:gost|гост|iso|iec|ieee\s+std|nema)\s*[-:]?\s*\d", low) or "regulation eu" in low or "регламент ес" in low:
        return "STANDARD"
    if re.search(r"\b(?:doctoral thesis|phd thesis|dissertation|диссертац|дис\.)\b", low):
        return "REPORT"
    if re.search(r"\b(?:technical report|tech rep|research report|white paper|system card|отчет|доклад)\b", low):
        return "REPORT"
    if re.search(r"\b(?:dataset|data set|набор данных)\b", low) and not re.search(r"\b(?:proceedings|journal|conference)\b", low):
        return "DATASET"
    if re.search(r"\b(?:book|handbook|textbook|monograph|монограф|учебное пособие|учеб пособие)\b", low) or re.search(r"\b(?:apress|mit press|cambridge university press|academic press|wiley)\b", low):
        if not re.search(r"\b(?:proceedings|conference|journal)\b", low):
            return "BOOK"

    # Scholarly signals take precedence over words such as "documentation" in a
    # paper title and over URLs embedded in the citation.
    paper_markers = [
        "proceedings", "conference", "journal", "transactions", "symposium", "workshop",
        "ieee software", "ieee/acm", "acm ", "neurips", "nips", "icml", "iclr",
        "cvpr", "iccv", "eccv", "acl", "emnlp", "aaai", "enase", "plos one",
        "advances in neural information processing systems", "association for computational linguistics",
        "natural language generation conference", "software engineering", "program comprehension",
    ]
    # GOST uses // as title/venue separator. Do not confuse it with https://.
    gost_venue_separator = bool(re.search(r"(?<!:)//\s*\S", ref))
    if legacy_kind == "paper" or any(marker in low for marker in paper_markers) or gost_venue_separator:
        return "PAPER"
    if extract_doi(ref):
        return "PAPER"

    # Stable web/software categories are source types, not negative verdicts.
    url = extract_url(ref).casefold()
    if (
        host.startswith("docs.")
        or host in {"learn.microsoft.com", "google.github.io", "readthedocs.io"}
        or "/docs/" in url
        or "/documentation/" in url
        or "official documentation" in low
        or "developer guide" in low
        or "user guide" in low
        or "style guide" in low
        or "training module" in low
    ):
        return "DOCUMENTATION"
    if host == "github.com" or "github repository" in low:
        return "REPOSITORY"

    if extract_url(ref):
        return "WEB"
    if legacy_kind == "bibliographic":
        return "OTHER"
    return "UNKNOWN"


def source_type_label(source_type: str) -> str:
    return {
        "PAPER": "Статья",
        "PREPRINT": "Препринт",
        "BOOK": "Книга",
        "STANDARD": "Стандарт",
        "REPORT": "Отчёт",
        "DATASET": "Набор данных",
        "DOCUMENTATION": "Документация",
        "REPOSITORY": "Репозиторий",
        "WEB": "Веб-источник",
        "OTHER": "Другой источник",
        "UNKNOWN": "Тип не определён",
    }.get(source_type, "Тип не определён")


def verdict_from_status(status: str) -> str:
    status = (status or "").upper()
    if status in {"OK", "OK_MINOR_MISMATCH"}:
        return "VERIFIED"
    if status in {"METADATA_MISMATCH", "SUSPICIOUS", "LIKELY_HALLUCINATED"}:
        return "SUSPICIOUS"
    if status == "ERROR":
        return "ERROR"
    return "UNVERIFIED"


def candidate_has_metadata(candidate: dict[str, Any]) -> bool:
    return bool(
        str(candidate.get("title") or "").strip()
        or str(candidate.get("doi") or "").strip()
        or str(candidate.get("arxiv_id") or "").strip()
        or str(candidate.get("authors") or "").strip()
    )
