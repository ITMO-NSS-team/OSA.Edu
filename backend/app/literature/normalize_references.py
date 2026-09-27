#!/usr/bin/env python3
"""Normalize extracted bibliography .txt files into one-reference-per-record files.

Input files contain bibliography text extracted from PDFs (<PDF basename>.txt).
Outputs are written to references_normalized/:
  - <basename>.refs.txt   human-readable numbered references, one per block
  - <basename>.refs.jsonl machine-readable records
  - all_refs.tsv          combined table for batching/checking
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BRACKET_ENTRY_START_RE = re.compile(r"^\s*\[([A-Za-zА-Яа-я]?\d+)\]\s*(.*)$")
NUMBER_ENTRY_START_RE = re.compile(r"^\s*(\d+)[.)][\s\u200b\ufeff]+(.*)$")
HEADER_RE = re.compile(
    r"^(?:# Extracted from:|# Mode:|\s*(?:\d+(?:\.\d+)*[.)]?\s*)?(?:список\s+(?:(?:использованных|использованной|использованых)\s+)?(?:источников|литературы)(?:\s+и\s+литературы)?|библиографический\s+список|библиография|литература|references|bibliography)\s*$)",
    re.I,
)
PAGE_NO_RE = re.compile(r"^\s*\d{1,4}\s*$")
LINE_NO_PREFIX_RE = re.compile(r"^\s*\d{1,5}\s{2,}(?=\S)(.*)$")
FOOTER_RE = re.compile(
    r"(?:confidential reviewer copy|unauthorized|sharing, redistribution, or disclosure|^\s*MM\s+[’']\d{2},.*$|^\s*[A-ZА-ЯЁ][\w.\-]+(?:\s+[A-ZА-ЯЁ][\w.\-]+)*\s+et\s+al\.\s*$|^\s*Лист\s*$|^\s*ВКР\([^\n]*\)\S*.*\s+\d+\s*$|^\s*Изм\s+Лист\s+№\s*докум\.\s+Подпись\s+Дата\s*$|^\s*\d{1,4}\s+[A-ZА-ЯЁ][\w.\- ]+\s+et\s+al\.\s*$|^\s*[A-ZА-ЯЁ][^\n]{20,}\s{2,}\d{1,4}\s*$)",
    re.I,
)
URL_SPLIT_RE = re.compile(r"(https?):\s+//")
SPACE_RE = re.compile(r"[ \t]+")
DOI_SPACING_RE = re.compile(r"\b(DOI:\s*)(10\s*\.\s*\d{4,9}\s*/\s*[-._;()/:A-Z0-9]+(?:\s+[-._;()/:]*(?:\d|[A-Z]+\d)[-._;()/:A-Z0-9]*)*)", re.I)
YEAR_RE = re.compile(r"\b(?:18|19|20)\d{2}[a-z]?\b", re.I)
AUTHOR_ENTRY_START_RE = re.compile(
    r"^[A-ZА-ЯЁÀ-ÖØ-Þ][A-Za-zА-Яа-яЁёÀ-ÖØ-öø-ÿ'’.-]{0,60},\s*"
    r"[A-ZА-ЯЁ]\.(?:\s*[A-ZА-ЯЁ]\.)?"
)
CORPORATE_ENTRY_START_RE = re.compile(
    r"^(?:[A-ZА-ЯЁ]\.){2,}\s*[A-ZА-ЯЁ]?[A-Za-zА-Яа-яЁёÀ-ÖØ-öø-ÿ'’.-]*.*"
    r"\b(?:18|19|20)\d{2}[a-z]?\."
)


def clean_line(line: str) -> str | None:
    line = line.replace("\f", "").rstrip()
    if not line.strip():
        return ""
    if FOOTER_RE.search(line):
        return None
    line = LINE_NO_PREFIX_RE.sub(r"\1", line)
    if HEADER_RE.match(line.strip()):
        return None
    if PAGE_NO_RE.match(line):
        return None
    return line


def append_wrapped(current: str, line: str) -> str:
    line = line.strip()
    if not current:
        return line

    # Undo PDF line-break hyphenation: "Gal-\nteri" -> "Galteri".
    # If the next fragment is itself hyphenated ("Bag-\nof-Freebies"), keep the
    # line-break hyphen because it is likely part of a compound.
    if current.endswith("-") and line and line[0].isalpha():
        if re.search(r"(?:https?\s*:|https?://|www\.)[A-Za-z0-9\s:/.?&=#%_-]*-$", current, flags=re.I):
            return current + line
        if re.search(r"\bdoi\s*:\s*\S*-$", current, flags=re.I):
            return current + line
        first_word = line.split(None, 1)[0]
        previous_word = current.rsplit(None, 1)[-1][:-1].lower()
        hyphenated_prefixes = {"anti", "co", "cross", "diffusion", "high", "low", "multi", "non", "pre", "post", "quasi", "real", "semi", "self", "test"}
        if line[0].isupper() or "-" in previous_word or previous_word in hyphenated_prefixes or ("-" in first_word and "/" not in first_word):
            return current + line
        return current[:-1] + line

    # Keep URL protocol split by pdftotext: "https: //..." -> "https://...".
    joined = current + " " + line
    joined = URL_SPLIT_RE.sub(r"\1://", joined)
    return joined


def normalize_text(text: str) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    current_num: str | None = None
    current = ""

    for raw in text.splitlines():
        line = clean_line(raw)
        if line is None:
            continue

        bracket_match = BRACKET_ENTRY_START_RE.match(line)
        number_match = NUMBER_ENTRY_START_RE.match(line)
        if bracket_match or number_match:
            match = bracket_match or number_match
            parsed_label = match.group(1)
            parsed_num = int(re.search(r"\d+", parsed_label).group(0))
            parsed_rest = match.group(2).strip()
            # Avoid mistaking wrapped page ranges like "700—\n708." or volume/page
            # fragments like "27. — C. ..." for new references. Plain numbered entries
            # must have whitespace after the dot/paren, so decimals like "6.5" are not
            # treated as new references. New reference numbers should also increase by 1.
            current_int = int(re.search(r"\d+", current_num).group(0)) if current_num else None
            same_prefix = current_num is None or re.sub(r"\d+", "", parsed_label) == re.sub(r"\d+", "", current_num)
            restarts_with_prefix = current_num is not None and not same_prefix and parsed_num == 1
            if 1 <= parsed_num < 300 and ((same_prefix and (current_int is None or parsed_num == current_int + 1)) or restarts_with_prefix):
                if current_num is not None:
                    refs.append({"number": current_num, "reference": final_clean(current)})
                current_num = parsed_label
                current = parsed_rest
                continue

        if current_num is None:
            continue
        if line == "":
            continue
        current = append_wrapped(current, line)

    if current_num is not None:
        refs.append({"number": current_num, "reference": final_clean(current)})

    if refs:
        return refs
    return normalize_unnumbered_text(text)


def normalize_unnumbered_text(text: str) -> list[dict[str, object]]:
    """Parse bibliography styles where entries are not numbered.

    These PDFs use hanging indentation: the first line of each reference starts at
    column 0, while wrapped continuation lines are indented. Blank lines also
    separate many entries, but not all (notably across page breaks), so indentation
    is the most reliable signal.
    """
    refs: list[dict[str, object]] = []
    current = ""
    raw_lines = text.splitlines()
    content_lines = [raw for raw in raw_lines if raw.strip() and clean_line(raw) not in (None, "")]
    has_hanging_indent = any(raw[:1].isspace() for raw in content_lines) and any(
        not raw[:1].isspace() for raw in content_lines
    )

    for raw in raw_lines:
        line = clean_line(raw)
        if line is None:
            continue
        if line == "":
            continue

        if has_hanging_indent:
            starts_new = bool(raw.strip()) and not raw[:1].isspace()
        else:
            # PyMuPDF often drops hanging indentation entirely. In author-year
            # bibliographies every visual line then appears to start at column 0,
            # which previously turned one wrapped citation into many fragments.
            # Split only after the current entry has acquired a year and the next
            # line looks like a personal or corporate author heading.
            starts_new = bool(current) and bool(YEAR_RE.search(current)) and bool(
                AUTHOR_ENTRY_START_RE.match(line) or CORPORATE_ENTRY_START_RE.match(line)
            )
        if starts_new and current:
            refs.append({"number": str(len(refs) + 1), "reference": final_clean(current)})
            current = line.strip()
        else:
            current = append_wrapped(current, line)

    if current:
        refs.append({"number": str(len(refs) + 1), "reference": final_clean(current)})
    return refs


def final_clean(s: str) -> str:
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = SPACE_RE.sub(" ", s)

    # Default pdftotext reading order handles multi-column bibliographies well,
    # but occasionally drops a line-break hyphen entirely.
    dropped_hyphen_repairs = {
        "EeChien": "Ee-Chien",
        "HighCapacity": "High-Capacity",
        "SylvestreAlvise": "Sylvestre-Alvise",
        "deep-learningbased": "deep-learning-based",
        "invisiblewatermark": "invisible-watermark",
        "text-toimage": "text-to-image",
        "Treering": "Tree-ring",
        "treering": "tree-ring",
    }
    for damaged, repaired in dropped_hyphen_repairs.items():
        s = s.replace(damaged, repaired)
    s = re.sub(r"(?<=\d)([–-])\s+(?=\d)", r"\1", s)
    s = re.sub(r"(?<=\w)([–-])\s+(?=\w)", r"\1", s)
    s = re.sub(r"\bh\s+(ttps?://)", r"h\1", s)
    s = re.sub(r"(https?)\s*:\s*/\s*/", r"\1://", s)
    s = re.sub(r"(https?://)\s+", r"\1", s)
    s = re.sub(r"arXiv:\s*(\d{4})\s*\.\s*(\d{4,5})", r"arXiv:\1.\2", s, flags=re.I)

    # Repair common pdftotext spacing inside DOI fields without joining normal
    # prose after the DOI. The continuation fragments must begin with digits,
    # which catches split numeric DOI suffix chunks such as "net. 20371" and
    # "s10208- 015- 9296- 2".
    def fix_doi(m: re.Match[str]) -> str:
        return m.group(1) + re.sub(r"\s+", "", m.group(2))

    s = DOI_SPACING_RE.sub(fix_doi, s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)

    def fix_url(m: re.Match[str]) -> str:
        url = m.group(0)

        def collapse_url_spacing(fragment: str) -> str:
            fragment = re.sub(r"\s*([/:._?&=#%-])\s*", r"\1", fragment)
            return re.sub(r"\s+", "", fragment)

        # The broad URL-spacing regex can also catch a normal sentence that
        # follows a punctuated URL, e.g. "https://doi.org/10.x/id. Dataset".
        # Keep that sentence boundary while still repairing genuinely split URLs.
        sentence_boundary = re.search(r"(?<=\.)\s+(?=\S)", url)
        if sentence_boundary:
            tail = url[sentence_boundary.end() :]
            first_tail_token = re.split(r"\s+", tail, maxsplit=1)[0].strip("/._?&=#%-")
            url_continuations = {
                "ai", "aspx", "com", "edu", "gov", "htm", "html", "io",
                "net", "org", "pdf", "php", "ru",
            }
            if first_tail_token[:1].isupper() and first_tail_token.lower() not in url_continuations:
                head = collapse_url_spacing(url[: sentence_boundary.start()])
                return head + " " + tail

        return collapse_url_spacing(url)

    s = re.sub(r"https?://[^\s,;)]+(?:\s+(?!(?i:and|or|url|doi|dataset|includes|available|accessed|дата)\b)[/A-Za-z0-9._?&=#%-]+)+", fix_url, s)
    return s.strip()


def is_obvious_nonpaper_source(ref: str) -> bool:
    low = ref.lower()
    nonpaper_markers = [
        "github.com",
        "github repository",
        "huggingface.co/datasets",
        "model card",
        "documentation",
        "docs.",
        "developer guide",
        "user guide",
        "online,",
        "open-source vector",
    ]
    return any(m in low for m in nonpaper_markers)


def has_explicit_paper_signal(ref: str) -> bool:
    low = ref.lower()
    paper_markers = [
        "// proceedings",
        "// ieee",
        "// acm",
        "// arxiv",
        "arxiv:",
        "arxiv abs/",
        "arxiv.org/abs/",
        "arxiv.org/pdf/",
        "corr abs/",
        "proceedings",
        "conference",
        "journal",
        "transactions",
        "lecture notes",
        "advances in neural information processing systems",
        "association for computational linguistics",
        "computational linguistics",
        "acm computing surveys",
        "knowledge and information systems",
        "sar and qsar in environmental research",
        "plos one",
        "mathematics of control, signals and systems",
        "preprints",
        "siam review",
        "nature ",
        "ai open",
        "foundations and trends",
        "psychology of learning and motivation",
        "evolutionary computation",
        "technometrics",
        "machine learning",
        "mach. learn.",
        "jmlr",
        "cvpr",
        "iccv",
        "eccv",
        "neurips",
        "nips",
        "icml",
        "iclr",
        "acl",
        "emnlp",
        "ijcai",
        "aaai",
        "siggraph",
        "workshop",
        "symposium",
        "журнал",
        "известия",
        "вестник",
        "письма в",
        "adv. sci.",
    ]
    structural_patterns = [
        r"\bin\s+proceedings\b",
        r"\b(?:arxiv|corr)\s+abs/\d{4}\.\d{4,5}\b",
        r"\b\d+\s*,\s*\d+(?:[-–]\d+)?\s*\([^)]*(?:19|20)\d{2}[^)]*\)\s*,\s*\d+\s*[–-]\s*\d+",
        r"\b\d+\s*\([^)]*(?:19|20)\d{2}[^)]*\)\s*,\s*\d+\s*[–-]\s*\d+",
        r"\b\d+\s*,\s*\d+\s*[–-]\s*\d+\s*\((?:19|20)\d{2}\)",
        r"\b(?:p|pp)\.\s*\d+\s*(?:[–-]\s*\d+)?\b",
        r"\bpages\s+\d+\s*[–-]\s*\d+\b",
        r"\b(?:19|20)\d{2}\s*;\s*\d+\s*:\s*\d+(?:\s*[–-]\s*\d+)?\b",
        # BibTeX-like article/chapter shape without obvious journal keywords:
        # "Container, 15(3):122-137, 1963" or "Container, 568:127063, 2024".
        # This also catches malformed volume/page strings such as
        # "15(122-137):3, 1963", which should still be sent through reference
        # verification rather than skipped as OTHER.
        r"\.\s+[^,]{5,},\s*\d+\s*(?:\([^)]*\)\s*)?:\s*\d+(?:\s*[–-]\s*\d+)?\s*,\s*(?:19|20)\d{2}\b",
        r"\.\s+[^,]{5,},\s*\d+\s*:\s*\d+(?:\s*[–-]\s*\d+)?\s*,\s*(?:\d{1,2}\s+)?(?:19|20)\d{2}\b",
    ]
    has_author_slash = " / " in ref and re.search(r"\[(?:et al\.|и др\.)\]", ref, flags=re.I)
    # In GOST-style thesis references, "//" usually separates paper title from venue.
    return any(m in low for m in paper_markers) or " // " in ref or has_author_slash or any(
        re.search(pattern, ref, flags=re.I) for pattern in structural_patterns
    )


def has_scholarly_author_title_shape(ref: str) -> bool:
    """Detect bare paper/preprint citations whose venue was omitted by pdftotext/BibTeX.

    ACM-style bibliographies often render unpublished/arXiv-like entries as only
    `Authors. Title, YEAR.`; these are still checkable scholarly works and should
    not be skipped as generic OTHER rows.
    """
    if is_obvious_nonpaper_source(ref):
        return False
    starts_with_gost_author = bool(re.match(r"^\s*[^\s,]{1,50},\s*[A-ZА-ЯЁ]\.", ref))
    starts_with_full_name_author = bool(re.match(r"^\s*[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+(?:\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,3}(?:,|\s+and\s+)", ref))
    starts_with_author = starts_with_gost_author or starts_with_full_name_author
    if not starts_with_author or ". " not in ref:
        return False

    author_mentions = len(re.findall(r"(?:^|,\s+)[^\s,]{1,50},\s*[A-ZА-ЯЁ]\.", ref))
    has_multiple_authors = author_mentions >= 2 or any(m in ref.lower() for m in [", and ", " et al.", "[et al.", "[и др."])
    has_year = bool(re.search(r"\b(?:19|20)\d{2}\b", ref))
    has_pages = bool(re.search(r"\b(?:p|pp)\.\s*\d+", ref, flags=re.I))
    # Either a dated author/title citation or a long multi-author title-only
    # citation is paper-like enough for the checker to verify.
    return has_pages or (has_year and len(ref) > 45) or (has_multiple_authors and len(ref) > 100)


def is_likely_paper(ref: str) -> bool:
    if is_obvious_nonpaper_source(ref):
        return False
    return has_explicit_paper_signal(ref) or has_scholarly_author_title_shape(ref)


def is_bibliographic_work(ref: str) -> bool:
    """Return true for non-paper works whose bibliographic data can be checked.

    These are still outside the scholarly-paper bucket, but they should not be
    skipped as NOT_A_PAPER: books, standards, legal acts, reports, dissertations,
    and technical reports have stable titles/creators/years/publishers or
    identifiers that can be verified in the same table as papers.
    """
    low = ref.lower()
    if any(m in low for m in ["github.com", "docs.", "documentation", "developer guide", "user guide", "dataset curation"]):
        return False

    patterns = [
        r"\bгост\b",
        r"\biso\s*\d",
        r"\bnema\s+ps\d",
        r"\bregulation\s*\(eu\)",
        r"\bрегламент\s*\(ес\)",
        r"\b(?:book|handbook|textbook|monograph)\b",
        r"\b(?:учеб\.?\s*пособие|учебное\s+пособие|монограф(?:ия|ии|ию)|изд\.)\b",
        r"^\s*[А-ЯЁ][^,]{1,50},\s*[А-ЯЁ]\.?.*\b(?:19|20)\d{2}\b",
        r"\b(?:press|springer|apress|elsevier|academic press|mit press|cambridge university press|chapman\s*&\s*hall|crc|addison-wesley|wiley)\b",
        r"\b\d+(?:st|nd|rd|th)?\s+ed\.",
        r"\b\d+\s*p\.",
        r"\b\d+\s*с\.",
        r"\b(?:report|доклад|статистический сборник|dataset)\b",
        r"\bzenodo\b",
        r"\b(?:blog|system card)\b",
        r"\bmarket\s+(?:size|share|report|research)\b",
        r"\b(?:ken research|grand view research)\b",
        r"(?:\btechnical report\b|\btech\.\s*rep\.|тех\.\s*отч\.|\bmsr-tr-\d)",
        r"(?:\bdissertation\b|\bdoctoral thesis\b|дис\.)",
    ]
    return any(re.search(pattern, low, flags=re.I) for pattern in patterns)


def classify_reference(ref: str) -> str:
    low = ref.lower()
    nonpaper_bibliographic_hint = any(
        re.search(pattern, low, flags=re.I)
        for pattern in [
            r"\bгост\b",
            r"\biso\s*\d",
            r"\bnema\s+ps\d",
            r"\bregulation\s*\(eu\)",
            r"\bрегламент\s*\(ес\)",
            r"\b(?:report|доклад|статистический сборник)\b",
            r"\bsystem card\b",
            r"(?:\btechnical report\b|\btech\.\s*rep\.|тех\.\s*отч\.|\bmsr-tr-\d)",
            r"(?:\bdissertation\b|\bdoctoral thesis\b|дис\.)",
        ]
    )
    bibliographic = is_bibliographic_work(ref)
    # Book/report-style citations can look like bare author/title/year preprints.
    # Keep those in the bibliographic bucket unless there is an explicit article
    # or proceedings signal.
    if bibliographic and (nonpaper_bibliographic_hint or not has_explicit_paper_signal(ref)):
        return "bibliographic"
    if is_likely_paper(ref) and not nonpaper_bibliographic_hint:
        return "paper"
    if bibliographic:
        return "bibliographic"
    if is_likely_paper(ref):
        return "paper"
    return "other"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="*", type=Path, help="extracted bibliography .txt files")
    parser.add_argument("--out-dir", type=Path, default=Path("references_normalized"))
    args = parser.parse_args()

    inputs = args.inputs or sorted(
        p for p in Path.cwd().glob("*.txt") if not p.name.endswith((".refs.txt", ".refs.jsonl"))
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)

    all_rows: list[tuple[str, str, str, str]] = []
    for path in inputs:
        text = path.read_text(encoding="utf-8")
        refs = normalize_text(text)
        stem = path.stem

        txt_out = args.out_dir / f"{stem}.refs.txt"
        jsonl_out = args.out_dir / f"{stem}.refs.jsonl"

        with txt_out.open("w", encoding="utf-8") as f:
            for index, rec in enumerate(refs):
                kind = classify_reference(str(rec["reference"]))
                if index:
                    f.write("\n")
                f.write(f"[{rec['number']}] {kind.upper()}\n{rec['reference']}\n")
                all_rows.append((stem, str(rec["number"]), kind, str(rec["reference"])))

        with jsonl_out.open("w", encoding="utf-8") as f:
            for rec in refs:
                ref = str(rec["reference"])
                out = {
                    "thesis": stem,
                    "number": rec["number"],
                    "kind": classify_reference(ref),
                    "reference": ref,
                }
                f.write(json.dumps(out, ensure_ascii=False) + "\n")

        print(f"OK {path.name}: {len(refs)} refs -> {txt_out} / {jsonl_out}")

    tsv_out = args.out_dir / "all_refs.tsv"
    with tsv_out.open("w", encoding="utf-8") as f:
        f.write("thesis\tnumber\tkind\treference\n")
        for thesis, number, kind, ref in all_rows:
            f.write(f"{thesis}\t{number}\t{kind}\t{ref.replace(chr(9), ' ')}\n")
    print(f"Wrote combined table: {tsv_out} ({len(all_rows)} refs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
