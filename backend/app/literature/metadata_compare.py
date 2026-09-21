from __future__ import annotations

import difflib
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from .metadata import extract_arxiv_id, extract_doi, extract_pages, extract_publication_years, extract_url, normalize_text


def _tokens(value: str) -> set[str]:
    return {token for token in normalize_text(value).split() if len(token) > 2}


def title_similarity(a: str, b: str) -> float:
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    seq = difflib.SequenceMatcher(None, na, nb).ratio()
    ta, tb = _tokens(na), _tokens(nb)
    if not ta or not tb:
        return seq
    inter = len(ta & tb)
    containment = inter / max(1, min(len(ta), len(tb)))
    jaccard = inter / max(1, len(ta | tb))
    return max(seq, 0.72 * containment + 0.28 * jaccard)


def _canonical_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        path = parts.path.rstrip("/") or "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))
    except Exception:
        return url.strip().rstrip("/").lower()


def _citation_author_surnames(reference: str, cited_title: str) -> set[str]:
    prefix = reference
    if cited_title and cited_title in reference:
        prefix = reference.split(cited_title, 1)[0]
    # In inverted Russian/English citation styles surnames are the tokens directly
    # before initials. This deliberately avoids treating title words as authors.
    names = re.findall(r"\b([A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\-]{1,50})\s+(?=[A-ZА-ЯЁ](?:\.|\s+[A-ZА-ЯЁ]\.))", prefix)
    if not names:
        # Full-name style: `Tom B. Brown, Dandelion Mané, ...`.
        chunks = re.split(r",\s+(?:and\s+)?|\s+and\s+", prefix.strip(" .,"))
        for chunk in chunks:
            words = re.findall(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+", chunk)
            if words:
                names.append(words[-1])
    return {normalize_text(name) for name in names if normalize_text(name)}


def _candidate_author_surnames(authors: str) -> set[str]:
    result: set[str] = set()
    for part in re.split(r";|,\s+(?=[A-ZА-ЯЁ])", authors or ""):
        words = re.findall(r"[A-Za-zА-Яа-яЁёÀ-ÖØ-öø-ÿ'’\-]+", part)
        if words:
            result.add(normalize_text(words[-1]))
    return {x for x in result if x}


def author_overlap(reference: str, cited_title: str, authors: str) -> float | None:
    cited = _citation_author_surnames(reference, cited_title)
    found = _candidate_author_surnames(authors)
    if not cited or not found:
        return None
    return len(cited & found) / max(1, min(len(cited), len(found)))


def _candidate_year(candidate: dict[str, Any]) -> int | None:
    match = re.search(r"\b(?:19|20)\d{2}\b", str(candidate.get("year") or ""))
    return int(match.group(0)) if match else None


def _year_relation(reference: str, candidate: dict[str, Any]) -> str:
    cited = [int(y) for y in extract_publication_years(reference)]
    found = _candidate_year(candidate)
    if not cited or found is None:
        return "unknown"
    if found in cited:
        return "exact"
    if min(abs(found - year) for year in cited) <= 1:
        return "adjacent"
    return "conflict"


def _pages_relation(reference: str, candidate: dict[str, Any]) -> str:
    cited = extract_pages(reference)
    found_raw = str(candidate.get("pages") or "")
    found_match = re.search(r"(\d+)\s*[–—-]\s*(\d+)", found_raw)
    if not cited or not found_match:
        return "unknown"
    found = f"{found_match.group(1)}-{found_match.group(2)}"
    return "exact" if cited == found else "conflict"


def _exact_locator(reference: str, candidate: dict[str, Any]) -> bool:
    cited_doi = extract_doi(reference)
    found_doi = str(candidate.get("doi") or "").lower().strip()
    if cited_doi and found_doi:
        return cited_doi == found_doi
    cited_arxiv = extract_arxiv_id(reference)
    found_arxiv = str(candidate.get("arxiv_id") or "").lower().strip()
    if cited_arxiv and found_arxiv:
        return cited_arxiv == found_arxiv
    cited_url = _canonical_url(extract_url(reference))
    found_url = _canonical_url(str(candidate.get("url") or ""))
    return bool(cited_url and found_url and cited_url == found_url)




def _locator_kind(reference: str, candidate: dict[str, Any]) -> str:
    cited_doi = extract_doi(reference)
    found_doi = str(candidate.get("doi") or "").lower().strip()
    if cited_doi and found_doi and cited_doi == found_doi:
        return "doi"
    cited_arxiv = extract_arxiv_id(reference)
    found_arxiv = str(candidate.get("arxiv_id") or "").lower().strip()
    if cited_arxiv and found_arxiv and cited_arxiv == found_arxiv:
        return "arxiv"
    cited_url = _canonical_url(extract_url(reference))
    found_url = _canonical_url(str(candidate.get("url") or ""))
    if cited_url and found_url and cited_url == found_url:
        return "url"
    return "none"


def _has_person_author_claim(reference: str, cited_title: str) -> bool:
    prefix = reference
    if cited_title and cited_title in reference:
        prefix = reference.split(cited_title, 1)[0]
    prefix = prefix.strip(" ,;:")
    if not prefix:
        return False
    if re.search(r"^\s*[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\-]{1,50}\s+(?:[A-ZА-ЯЁ]\.\s*){1,4}", prefix):
        return True
    if re.search(r"^\s*[A-ZА-ЯЁ][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+(?:\s+[A-Z]\.?|\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+){1,3}(?:,|\s+and\s+)", prefix):
        return True
    return bool(re.search(r"\bet\s+al\.?$", prefix, flags=re.I))


def compare_candidate(row: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    reference = str(row.get("reference") or "")
    cited_title = str(row.get("cited_title") or "")
    found_title = str(candidate.get("title") or "")
    title_score = title_similarity(cited_title, found_title)
    author_score = author_overlap(reference, cited_title, str(candidate.get("authors") or ""))
    year_relation = _year_relation(reference, candidate)
    pages_relation = _pages_relation(reference, candidate)
    exact_locator = _exact_locator(reference, candidate)
    return {
        "title_score": title_score,
        "author_score": author_score,
        "year_relation": year_relation,
        "pages_relation": pages_relation,
        "exact_locator": exact_locator,
        "locator_kind": _locator_kind(reference, candidate),
    }


def deterministic_decision(row: dict[str, Any]) -> dict[str, Any] | None:
    """Return a high-confidence decision, otherwise defer to GLM/web verification."""
    candidates = [c for c in (row.get("candidates") or []) if isinstance(c, dict)]
    if not candidates:
        return None

    source_type = str(row.get("source_type") or "UNKNOWN").upper()
    scored: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for index, candidate in enumerate(candidates):
        cmp = compare_candidate(row, candidate)
        scored.append((index, candidate, cmp))
    scored.sort(key=lambda item: (bool(item[2]["exact_locator"]), float(item[2]["title_score"])), reverse=True)
    index, candidate, cmp = scored[0]
    title_score = float(cmp["title_score"])
    author_score = cmp["author_score"]
    year_relation = str(cmp["year_relation"])
    pages_relation = str(cmp["pages_relation"])
    exact_locator = bool(cmp["exact_locator"])
    locator_kind = str(cmp.get("locator_kind") or "none")
    has_title = bool(str(candidate.get("title") or "").strip())

    # An unresolved URL/DOI/arXiv locator is a lead, never proof.
    if not has_title:
        return None

    serious_conflicts: list[str] = []
    if pages_relation == "conflict":
        serious_conflicts.append("диапазон страниц")
    if year_relation == "conflict":
        serious_conflicts.append("год")
    if author_score is not None and author_score < 0.5:
        serious_conflicts.append("авторы")

    if exact_locator and title_score < 0.55:
        return {
            "status": "SUSPICIOUS",
            "candidate_index": index,
            "notes": "Указанный идентификатор/URL ведёт на источник с существенно другим названием; требуется независимая веб-проверка.",
            "needs_web": True,
            "comparison": cmp,
        }

    # A direct landing-page URL is useful evidence, but it is weaker than an
    # exact DOI/arXiv record for metadata. If the citation claims a personal
    # author or publication year and the page does not expose that field, defer
    # to independent web verification instead of silently treating title-only
    # agreement as enough. This catches cases such as a real OpenReview page with
    # a wrong cited publication year or a real blog URL with a wrong author.
    claimed_person = _has_person_author_claim(str(row.get("reference") or ""), str(row.get("cited_title") or ""))
    claimed_years = extract_publication_years(str(row.get("reference") or ""))
    missing_claimed_author = claimed_person and not str(candidate.get("authors") or "").strip()
    missing_claimed_year = bool(claimed_years) and _candidate_year(candidate) is None

    # Generic web/documentation/repository sources are verified primarily via the
    # cited landing page, but only when the page exposes enough of the metadata
    # actually claimed by the citation.
    if source_type in {"WEB", "DOCUMENTATION", "REPOSITORY", "DATASET"} and exact_locator:
        if serious_conflicts and title_score >= 0.55:
            return {
                "status": "SUSPICIOUS",
                "candidate_index": index,
                "notes": "Указанная страница найдена, но её метаданные расходятся со ссылкой: " + ", ".join(serious_conflicts) + ". Требуется независимая веб-проверка.",
                "needs_web": True,
                "comparison": cmp,
            }
        if missing_claimed_author or missing_claimed_year:
            return {
                "status": "UNVERIFIED",
                "candidate_index": index,
                "notes": "Указанная страница существует, но на ней недостаточно метаданных для проверки заявленных автора/года; требуется независимая веб-проверка.",
                "needs_web": True,
                "comparison": cmp,
            }
        if title_score >= 0.72:
            return {
                "status": "OK",
                "candidate_index": index,
                "notes": "Указанная страница существует, её название и доступные метаданные согласуются со ссылкой в работе.",
                "needs_web": False,
                "comparison": cmp,
            }
        if title_score >= 0.55:
            return {
                "status": "OK_MINOR_MISMATCH",
                "candidate_index": index,
                "notes": "Страница подтверждена по указанному URL; название отличается только в оформлении или расширенной формулировке.",
                "needs_web": False,
                "comparison": cmp,
            }
        return None

    if exact_locator and title_score >= 0.88:
        if serious_conflicts:
            return {
                "status": "METADATA_MISMATCH",
                "candidate_index": index,
                "notes": "Работа однозначно найдена по указанному идентификатору, но расходятся существенные метаданные: " + ", ".join(serious_conflicts) + ".",
                "needs_web": locator_kind == "url",
                "comparison": cmp,
            }
        if locator_kind == "url" and (missing_claimed_author or missing_claimed_year):
            return {
                "status": "UNVERIFIED",
                "candidate_index": index,
                "notes": "Указанная страница подтверждает название работы, но не содержит достаточно метаданных для проверки заявленных автора/года; требуется независимый веб-поиск.",
                "needs_web": True,
                "comparison": cmp,
            }
        status = "OK_MINOR_MISMATCH" if year_relation == "adjacent" else "OK"
        note = (
            "Работа подтверждена по указанному идентификатору; разница года совместима с жизненным циклом препринт/публикация."
            if status == "OK_MINOR_MISMATCH"
            else "Работа подтверждена по указанному идентификатору; название и основные метаданные согласуются."
        )
        return {"status": status, "candidate_index": index, "notes": note, "needs_web": False, "comparison": cmp}

    # Accept title-search matches only when independent metadata signals
    # agree; otherwise defer to the configured comparison model.
    author_ok = author_score is None or author_score >= 0.6
    year_ok = year_relation in {"exact", "adjacent", "unknown"}
    if title_score >= 0.96 and author_ok and year_ok and pages_relation != "conflict":
        status = "OK_MINOR_MISMATCH" if year_relation == "adjacent" else "OK"
        return {
            "status": status,
            "candidate_index": index,
            "notes": "Высокоточное совпадение названия и совместимые авторы/год в библиографическом источнике.",
            "needs_web": False,
            "comparison": cmp,
        }
    return None
