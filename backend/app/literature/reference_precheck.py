#!/usr/bin/env python3
"""Create a first-pass evidence sheet for bibliography hallucination checks.

This is intentionally conservative: it suggests likely matches from DOI/arXiv/Crossref,
but a human/agent must still open evidence links and verify metadata before assigning
final statuses in reference_checks/<thesis>.md.

Usage:
  python scripts/reference_precheck.py references_normalized/ГорностаевНЮ_.refs.jsonl
  python scripts/reference_precheck.py --checkable-only references_normalized/Some.refs.jsonl
  python scripts/reference_precheck.py --paper-only references_normalized/Some.refs.jsonl
  python scripts/reference_precheck.py --fresh references_normalized/Some.refs.jsonl
"""

from __future__ import annotations

import argparse
import csv
import difflib
import html
import ipaddress
import json
import os
import re
import socket
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx

from .metadata import extract_arxiv_id, extract_doi, extract_url
from .normalize_references import is_bibliographic_work, is_likely_paper

DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ARXIV_RE = re.compile(r"\b(?:arXiv:|abs/)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z-]+/\d{7}(?:v\d+)?)\b", re.I)
SPACE_RE = re.compile(r"\s+")
FIELDNAMES = ["number", "kind", "cited_title", "suggestion", "source", "authors", "title", "url", "venue", "year", "score", "reference", "candidates"]
CROSSREF_MAILTO = os.environ.get("CROSSREF_MAILTO", "").strip()


def norm_text(s: str) -> str:
    s = html.unescape(s).lower()
    s = re.sub(r"[^\w\s]", " ", s, flags=re.U)
    return SPACE_RE.sub(" ", s).strip()


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_text(a), norm_text(b)).ratio()


def looks_like_creator_fragment(s: str, *, short_org: bool = True) -> bool:
    """Heuristic for author/editor/organization fragments before a title."""
    s = s.strip()
    if not s:
        return False
    # Author lists in normalized refs usually contain inverted names and initials.
    if re.fullmatch(r"[A-ZА-ЯЁ]", s):
        return True
    if re.search(r"(?:^|,\s+)(?:and\s+)?[A-ZА-ЯЁ][\w'’.-]+,\s*[A-ZА-ЯЁ](?:\.|\b)", s):
        return True
    if "," in s and re.search(r"\b[A-ZА-ЯЁ]\.?(?:,|$)", s):
        return True
    if re.search(r"\bet al$", s, flags=re.I):
        return True
    # Single short organization/creator prefixes: OpenAI. Title. ...
    if short_org and "," not in s and len(s.split()) <= 4 and not re.search(r":", s):
        return True
    return False


def _strip_gost_authors(text: str) -> str:
    # `Surname A.B., Surname C. Title` and `Surname A. et al. Title`.
    # Keep this regex case-sensitive: IGNORECASE would greedily consume title words.
    surname = r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\-]{1,60}"
    initials = r"(?:[A-ZА-ЯЁ]\.\s*){1,4}"
    author = rf"{surname}\s+{initials}"
    pattern = re.compile(
        rf"^\s*(?:{author}\s*(?:,\s*|\s+(?i:and)\s+|\s+и\s+)?)+(?:\s*(?i:et\s+al\.)\s*)?"
    )
    stripped = pattern.sub("", text, count=1).strip(" .,")
    original = text.strip(" .,")
    if not stripped or stripped == original:
        return text.strip()
    # `Tom B. Brown, ...` is full-name style, not `Surname Initials.` style.
    # If removing one apparent GOST author leaves another capitalized surname
    # followed by a comma, keep the original and let the full-name parser handle it.
    if re.match(r"^[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё'’\-]+,\s+", stripped):
        return text.strip()
    return stripped


def extract_title(ref: str) -> str:
    """Best-effort title extraction for common GOST and English bibliography styles."""
    ref = SPACE_RE.sub(" ", ref.strip())
    if not ref:
        return ""

    # Electronic-resource metadata belongs to the citation, not to the title.
    core = re.split(r"\s*\[(?:Электронный ресурс|Electronic resource)\]", ref, maxsplit=1, flags=re.I)[0].strip()
    core = re.split(r"\s+(?:URL|Режим доступа)\s*:\s*https?://", core, maxsplit=1, flags=re.I)[0].strip()

    # GOST title/venue separator. The negative lookbehind avoids matching https://.
    if re.search(r"(?<!:)//\s*\S", core):
        pre = re.split(r"(?<!:)//\s*", core, maxsplit=1)[0].strip()
        title = _strip_gost_authors(pre)
    # GOST title / responsibility statement. Require spaces so URL slashes do not match.
    elif re.search(r"\s/\s", core):
        title = re.split(r"\s/\s", core, maxsplit=1)[0].strip()
        title = _strip_gost_authors(title)
    else:
        stripped = _strip_gost_authors(core)
        if stripped != core:
            title = stripped
        else:
            # Full-name bibliography style: split only after a sentence-ending
            # lowercase surname/word, not after initials such as `Tom B.`.
            parts = [p.strip() for p in re.split(r"(?<=[a-zà-öø-ÿа-яё])\.\s+", core) if p.strip()]
            first = parts[0] if parts else core
            # English full-name author lists may have commas (3+ authors) or
            # just an `and` between two authors: `Nicholas Carlini and David
            # Wagner. Towards evaluating ...`.  Detect the latter only when
            # both sides look like short person names, so titles containing an
            # ordinary conjunction are not stripped accidentally.
            two_full_names = False
            if " and " in first:
                person_parts = [part.strip() for part in re.split(r"\s+and\s+", first)]
                if len(person_parts) == 2:
                    person_re = re.compile(
                        r"^(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+|[A-Z]\.)"
                        r"(?:\s+(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’\-]+|[A-Z]\.)){1,3}$"
                    )
                    two_full_names = all(person_re.fullmatch(part) for part in person_parts)
            full_name_creator = bool(
                len(parts) > 1
                and (
                    re.search(r",\s+(?:and\s+)?[A-ZА-ЯЁ]", first)
                    or re.search(r"\bet\s+al$", first, flags=re.I)
                    or two_full_names
                    or looks_like_creator_fragment(first)
                )
            )
            start_index = 1 if full_name_creator else 0
            title = ""
            for part in parts[start_index:]:
                if re.match(r"(?:In\s+|Proceedings\b|IEEE\b|ACM\b|Advances\b|Journal\b|Transactions\b|Findings\b|Foundations\b|arXiv\b)", part, flags=re.I):
                    continue
                title = part
                break
            if not title:
                title = parts[start_index] if start_index < len(parts) else first

    # Strip trailing bibliographic fields that sometimes remain after title extraction.
    title = re.sub(r",\s*(?:19|20)\d{2}\b.*$", "", title)
    title = re.split(r"\s+[—–]\s+(?=(?:19|20)\d{2}\b|Vol\.|Т\.|URL:)", title, maxsplit=1, flags=re.I)[0]
    title = re.sub(r"^et\s+al\.\s+", "", title, flags=re.I).strip()
    title = title.replace("– Supplementary Material", "").replace("— Supplementary Material", "")
    return SPACE_RE.sub(" ", title).strip(" .")


def user_agent() -> str:
    if CROSSREF_MAILTO:
        return f"reference-check-precheck/1.0 (mailto:{CROSSREF_MAILTO})"
    return "reference-check-precheck/1.0"


def http_json(url: str, params: dict[str, str], timeout: int = 30) -> dict[str, Any] | None:
    query = dict(params)
    if "api.crossref.org" in url and CROSSREF_MAILTO:
        query.setdefault("mailto", CROSSREF_MAILTO)
    encoded = urllib.parse.urlencode(query)
    full = url + ("?" + encoded if encoded else "")
    req = urllib.request.Request(full, headers={"User-Agent": user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - diagnostic script
        return {"_error": str(e)}


def format_authors(authors: list[dict[str, Any]]) -> str:
    names: list[str] = []
    for a in authors:
        given = a.get("given", "")
        family = a.get("family", "")
        name = SPACE_RE.sub(" ", f"{given} {family}".strip())
        if name:
            names.append(name)
    return "; ".join(names)


def crossref_candidates(title: str, rows: int = 3, timeout: int = 30) -> list[dict[str, str]]:
    data = http_json("https://api.crossref.org/works", {"query.bibliographic": title, "rows": str(rows)}, timeout=timeout)
    if not data or "_error" in data:
        return []
    out: list[dict[str, str]] = []
    for item in data.get("message", {}).get("items", []):
        found_title = (item.get("title") or [""])[0]
        doi = item.get("DOI", "")
        url = f"https://doi.org/{doi}" if doi else item.get("URL", "")
        venue = (item.get("container-title") or [""])[0]
        year = ""
        for key in ("published-print", "published-online", "published", "issued"):
            parts = item.get(key, {}).get("date-parts")
            if parts and parts[0]:
                year = str(parts[0][0])
                break
        out.append({
            "source": "Crossref",
            "authors": format_authors(item.get("author", [])),
            "title": found_title,
            "url": url,
            "venue": venue,
            "year": year,
            "score": f"{similarity(title, found_title):.2f}",
            "doi": str(doi or "").lower(),
            "volume": str(item.get("volume") or ""),
            "issue": str(item.get("issue") or ""),
            "pages": str(item.get("page") or item.get("article-number") or ""),
        })
    return out


def arxiv_candidate(arxiv_id: str, timeout: int = 30) -> dict[str, str] | None:
    url = "https://export.arxiv.org/api/query"
    full = url + "?" + urllib.parse.urlencode({"id_list": arxiv_id})
    try:
        with urllib.request.urlopen(full, timeout=timeout) as r:
            root = ET.fromstring(r.read())
    except Exception:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        return None
    title = SPACE_RE.sub(" ", (entry.findtext("a:title", "", ns) or "")).strip()
    published = entry.findtext("a:published", "", ns) or ""
    eid = entry.findtext("a:id", "", ns) or f"https://arxiv.org/abs/{arxiv_id}"
    authors = "; ".join(
        SPACE_RE.sub(" ", (a.findtext("a:name", "", ns) or "")).strip()
        for a in entry.findall("a:author", ns)
        if (a.findtext("a:name", "", ns) or "").strip()
    )
    return {"source": "arXiv", "authors": authors, "title": title, "url": eid, "venue": "arXiv", "year": published[:4], "score": "1.00", "arxiv_id": arxiv_id.lower(), "identifier": f"arXiv:{arxiv_id}"}


def crossref_doi_candidate(doi: str, cited_title: str, timeout: int = 30) -> dict[str, str] | None:
    data = http_json("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="/"), {}, timeout=timeout)
    if not data or "_error" in data:
        return None
    item = data.get("message", {})
    found_title = (item.get("title") or [""])[0]
    venue = (item.get("container-title") or [""])[0]
    year = ""
    for key in ("published-print", "published-online", "published", "issued"):
        parts = item.get(key, {}).get("date-parts")
        if parts and parts[0]:
            year = str(parts[0][0])
            break
    resolved_doi = str(item.get("DOI", doi) or doi).lower()
    return {
        "source": "cited DOI/Crossref",
        "authors": format_authors(item.get("author", [])),
        "title": found_title,
        "url": f"https://doi.org/{resolved_doi}",
        "venue": venue,
        "year": year,
        "score": f"{similarity(cited_title, found_title):.2f}" if found_title else "",
        "doi": resolved_doi,
        "identifier": f"DOI:{resolved_doi}",
        "volume": str(item.get("volume") or ""),
        "issue": str(item.get("issue") or ""),
        "pages": str(item.get("page") or item.get("article-number") or ""),
    }


def candidate_json(candidates: list[dict[str, str]]) -> str:
    return json.dumps(candidates, ensure_ascii=False)


class _MetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.title_parts: list[str] = []
        self.meta: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True
            return
        if tag.lower() != "meta":
            return
        data = {str(k).lower(): str(v or "") for k, v in attrs}
        key = (data.get("name") or data.get("property") or data.get("itemprop") or "").strip().lower()
        value = data.get("content", "").strip()
        if key and value:
            self.meta.setdefault(key, []).append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and data.strip():
            self.title_parts.append(data.strip())


def _is_public_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        host = parsed.hostname.strip("[]").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        try:
            ip = ipaddress.ip_address(host)
            return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast)
        except ValueError:
            pass
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except Exception:
        return False


def _meta_first(meta: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        values = meta.get(key.lower()) or []
        if values and values[0].strip():
            return SPACE_RE.sub(" ", values[0]).strip()
    return ""


def direct_url_candidate(url: str, cited_title: str, timeout: int = 20) -> dict[str, str] | None:
    """Fetch metadata from the URL cited by the thesis itself.

    This is especially useful for official documentation, repositories, reports and
    publisher/OpenReview pages that Crossref cannot resolve. Redirect targets are
    validated to avoid turning citation checking into an SSRF primitive.
    """
    if not url or not _is_public_url(url):
        return None
    current = url
    try:
        with httpx.Client(
            timeout=timeout,
            headers={"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.8,*/*;q=0.5"},
            follow_redirects=False,
        ) as client:
            response = None
            for _ in range(5):
                if not _is_public_url(current):
                    return None
                response = client.get(current)
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location", "").strip()
                    if not location:
                        break
                    current = urllib.parse.urljoin(current, location)
                    continue
                break
            if response is None or response.status_code >= 400:
                return None
            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type and not response.text.lstrip().lower().startswith(("<!doctype html", "<html")):
                return {
                    "source": "cited URL",
                    "authors": "",
                    "title": "",
                    "url": current,
                    "venue": urllib.parse.urlparse(current).hostname or "",
                    "year": "",
                    "score": "",
                    "identifier": current,
                }
            parser = _MetaParser()
            parser.feed(response.text[:2_000_000])
    except Exception:
        return None

    title = _meta_first(
        parser.meta,
        "citation_title", "dc.title", "dcterms.title", "og:title", "twitter:title", "headline",
    ) or SPACE_RE.sub(" ", " ".join(parser.title_parts)).strip()
    # Remove common site suffixes without assuming a particular provider.
    if title and cited_title:
        for sep in (" | ", " — ", " - ", " · "):
            parts = [part.strip() for part in title.split(sep) if part.strip()]
            if len(parts) > 1:
                best = max(parts, key=lambda part: similarity(cited_title, part))
                if similarity(cited_title, best) > similarity(cited_title, title):
                    title = best

    authors = parser.meta.get("citation_author", []) or parser.meta.get("author", [])
    year_text = _meta_first(
        parser.meta,
        "citation_publication_date", "citation_date", "article:published_time", "date", "dc.date", "dcterms.date",
    )
    year_match = re.search(r"\b(?:19|20)\d{2}\b", year_text)
    venue = _meta_first(parser.meta, "citation_journal_title", "citation_conference_title", "og:site_name")
    return {
        "source": "cited URL",
        "authors": "; ".join(SPACE_RE.sub(" ", a).strip() for a in authors if str(a).strip()),
        "title": title,
        "url": current,
        "venue": venue or (urllib.parse.urlparse(current).hostname or ""),
        "year": year_match.group(0) if year_match else "",
        "score": f"{similarity(cited_title, title):.2f}" if title else "",
        "identifier": current,
    }


def best_evidence(ref: str, title: str, timeout: int = 30, source_type: str = "") -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    doi = extract_doi(ref)
    arxiv_id = extract_arxiv_id(ref)
    cited_url = extract_url(ref)

    # Exact identifiers first: they are the strongest and cheapest evidence.
    if doi:
        cand = crossref_doi_candidate(doi, title, timeout=timeout)
        if cand:
            candidates.append(cand)
        else:
            candidates.append({
                "source": "cited DOI",
                "authors": "",
                "title": "",
                "url": f"https://doi.org/{doi}",
                "venue": "",
                "year": "",
                "score": "",
                "doi": doi,
                "identifier": f"DOI:{doi}",
            })

    if arxiv_id:
        cand = arxiv_candidate(arxiv_id, timeout=timeout)
        if cand:
            candidates.append(cand)
        else:
            # Preserve the cited locator as a lead, but an unresolved locator is not
            # itself confirmation and will be sent to web verification.
            candidates.append({
                "source": "cited arXiv",
                "authors": "",
                "title": "",
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "venue": "arXiv",
                "year": "",
                "score": "",
                "arxiv_id": arxiv_id,
                "identifier": f"arXiv:{arxiv_id}",
            })

    if cited_url and not doi and not arxiv_id:
        direct = direct_url_candidate(cited_url, title, timeout=timeout)
        if direct:
            candidates.append(direct)
        else:
            candidates.append({
                "source": "cited URL",
                "authors": "",
                "title": "",
                "url": cited_url,
                "venue": urllib.parse.urlparse(cited_url).hostname or "",
                "year": "",
                "score": "",
                "identifier": cited_url,
            })

    # Crossref title search remains a useful fallback for scholarly/bibliographic
    # works, including citations with a broken locator. It is not used as the sole
    # verifier for generic web/documentation/repository sources.
    if source_type in {"PAPER", "PREPRINT", "BOOK", "REPORT", "OTHER", "UNKNOWN", ""} and title:
        for cand in crossref_candidates(title, rows=3, timeout=timeout):
            key = (cand.get("doi") or cand.get("url") or cand.get("title") or "").casefold()
            existing = {
                (c.get("doi") or c.get("url") or c.get("title") or "").casefold()
                for c in candidates
            }
            if key and key not in existing:
                candidates.append(cand)

    if not candidates:
        return {"source": "none", "authors": "", "title": "", "url": "", "venue": "", "year": "", "score": "", "candidates": ""}

    def rank(c: dict[str, str]) -> tuple[int, float]:
        exact = 2 if c.get("source") in {"cited DOI/Crossref", "arXiv"} and c.get("title") else 1 if c.get("source") == "cited URL" and c.get("title") else 0
        try:
            score = float(c.get("score") or 0)
        except Exception:
            score = 0.0
        return exact, score

    ordered = sorted(candidates, key=rank, reverse=True)
    best = dict(ordered[0])
    best["candidates"] = candidate_json(ordered[:5])
    return best


def is_checkable_reference(row: dict[str, Any]) -> bool:
    # Every extracted bibliography row gets an existence/metadata verdict.
    # `kind` is only a routing hint; it must never short-circuit verification.
    return bool(str(row.get("reference", "")).strip())


def sort_key_number(number: str) -> tuple[int, int | str]:
    """Sort numeric reference numbers before N-prefixed expansion numbers."""
    s = str(number)
    if s.isdigit():
        return (0, int(s))
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", s)
    if m:
        return (1, int(m.group(2)))
    return (2, s)


def format_candidate_links(r: dict[str, str]) -> str:
    candidates = []
    if r.get("candidates"):
        try:
            candidates = json.loads(r["candidates"])
        except json.JSONDecodeError:
            candidates = []
    if not candidates and r.get("url"):
        candidates = [r]
    if not candidates:
        return "no candidate"

    links: list[str] = []
    for i, c in enumerate(candidates[:3], 1):
        label = c.get("title") or c.get("source") or "candidate"
        url = c.get("url", "")
        score = c.get("score", "")
        suffix = f" (score {score})" if score else ""
        links.append(f"{i}. [{label}]({url}){suffix}" if url else f"{i}. {label}{suffix}")
    return "<br>".join(links)


def write_md(path: Path, checked: list[dict[str, str]]) -> None:
    with path.open("w") as f:
        f.write("| # | Kind | Cited title | Candidate evidence | Score | Suggested next step |\n")
        f.write("|---|------|-------------|--------------------|-------|---------------------|\n")
        for r in sorted(checked, key=lambda row: sort_key_number(row["number"])):
            link = format_candidate_links(r)
            step = "open/verify metadata" if r["suggestion"] != "LOW_CONFIDENCE" else "do exact-title web/publisher search"
            f.write(f"| {r['number']} | {r['kind']} | {r['cited_title']} | {link} | {r['score']} | {step} |\n")


def load_existing_tsv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl", type=Path)
    ap.add_argument("--checkable-only", action="store_true", help="skip rows that are neither papers nor bibliographically checkable works")
    ap.add_argument("--paper-only", action="store_true", help="strictly skip rows whose kind is not paper; usually prefer --checkable-only")
    ap.add_argument("--sleep", type=float, default=0.15, help="delay between network lookups")
    ap.add_argument("--request-timeout", type=int, default=30, help="per-request network timeout in seconds")
    ap.add_argument("--fresh", action="store_true", help="overwrite any existing precheck TSV instead of resuming")
    args = ap.parse_args()

    rows = [json.loads(line) for line in args.jsonl.read_text().splitlines() if line.strip()]
    thesis = rows[0].get("thesis", args.jsonl.stem) if rows else args.jsonl.stem
    outdir = Path("reference_checks")
    outdir.mkdir(exist_ok=True)
    tsv_path = outdir / f"{thesis}.precheck.tsv"
    md_path = outdir / f"{thesis}.precheck.md"

    should_resume = tsv_path.exists() and not args.fresh
    checked: list[dict[str, str]] = load_existing_tsv(tsv_path) if should_resume else []
    for r in checked:
        r.setdefault("candidates", "")

    filtered_checked = checked
    if args.paper_only:
        filtered_checked = [r for r in filtered_checked if r.get("kind") == "paper"]
    if args.checkable_only:
        filtered_checked = [r for r in filtered_checked if is_checkable_reference(r)]
    existing_filter_dropped_rows = len(filtered_checked) != len(checked)
    checked = filtered_checked

    if should_resume:
        with tsv_path.open(newline="") as f:
            existing_fields = next(csv.reader(f, delimiter="\t"), [])
        if existing_fields != FIELDNAMES or existing_filter_dropped_rows:
            with tsv_path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter="\t", lineterminator="\n", extrasaction="ignore")
                w.writeheader()
                w.writerows(checked)

    completed = {r["number"] for r in checked}
    mode = "a" if should_resume else "w"

    with tsv_path.open(mode, newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter="\t", lineterminator="\n")
        if mode == "w":
            w.writeheader()
        for row in rows:
            number = str(row["number"])
            if args.paper_only and row.get("kind") != "paper":
                continue
            if args.checkable_only and not is_checkable_reference(row):
                continue
            if number in completed:
                continue
            title = extract_title(row["reference"])
            ev = best_evidence(row["reference"], title, timeout=args.request_timeout)
            score = float(ev["score"] or 0) if ev["score"] else None
            suggestion = "REVIEW"
            if ev["source"] == "arXiv" and score is not None and score >= 0.90:
                suggestion = "LIKELY_OK"
            elif ev["url"].startswith("https://doi.org/") and (score is None or score >= 0.82):
                suggestion = "LIKELY_OK"
            elif score is not None and score < 0.75:
                suggestion = "LOW_CONFIDENCE"
            record = {
                "number": number,
                "kind": row.get("kind", ""),
                "cited_title": title,
                "suggestion": suggestion,
                **ev,
                "reference": row["reference"],
            }
            checked.append(record)
            completed.add(number)
            w.writerow(record)
            f.flush()
            write_md(md_path, checked)
            print(f"checked #{number}: {suggestion}", flush=True)
            time.sleep(args.sleep)

    write_md(md_path, checked)
    print(f"wrote {tsv_path}")
    print(f"wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
