from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pymupdf


def normalize_text(value: str) -> str:
    return (
        value.replace("\r", "")
        .replace("\u00a0", " ")
        .replace("\u200b", "")
        .replace("\u2060", "")
        .strip()
    )


def extract_pdf(pdf_path: Path) -> dict[str, Any]:
    document = pymupdf.open(pdf_path)

    try:
        if document.needs_pass:
            raise RuntimeError("PDF защищён паролем и не может быть прочитан без пароля.")

        pages: list[dict[str, Any]] = []
        empty_pages: list[int] = []

        for page_index, page in enumerate(document):
            page_number = page_index + 1
            text = normalize_text(page.get_text("text", sort=True))

            if not text:
                empty_pages.append(page_number)

            pages.append(
                {
                    "number": page_number,
                    "text": text,
                }
            )

        warnings: list[str] = []
        if empty_pages:
            warnings.append(
                "На страницах без текстового слоя потребуется OCR: "
                + ", ".join(str(number) for number in empty_pages)
                + "."
            )

        return {
            "engine": "pymupdf",
            "engineVersion": str(getattr(pymupdf, "VersionBind", "unknown")),
            "pageCount": len(pages),
            "pages": pages,
            "warnings": warnings,
        }
    finally:
        document.close()


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: extract_pdf.py <file.pdf>")

    pdf_path = Path(sys.argv[1]).expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF не найден: {pdf_path}")

    result = extract_pdf(pdf_path)
    json.dump(result, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"[PyMuPDF] {error}", file=sys.stderr)
        raise
