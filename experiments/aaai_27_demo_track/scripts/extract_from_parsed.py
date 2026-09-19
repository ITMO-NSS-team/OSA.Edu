from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from osa_tool.config.settings import ConfigManager
from osa_tool.core.llm.llm import ModelHandlerFactory
from osa_tool.operations.analysis.artifacts import model_provenance
from osa_tool.operations.analysis.paper_claims import ClaimExtractor, MarkdownSectionParser, PaperSection
from osa_tool.utils.logger import logger, setup_logging


def _build_config_args(args: argparse.Namespace) -> argparse.Namespace:
    """Provide the small subset of normal CLI args consumed by ConfigManager."""
    return argparse.Namespace(
        api=args.api,
        base_url=args.base_url,
        config_file=args.config_file,
        context_window=args.context_window,
        max_retries=None,
        max_tokens=args.max_tokens,
        model=args.model,
        model_paper_claims=args.model,
        repository="https://github.com/aimclub/OSA",
        temperature=args.temperature,
        top_p=args.top_p,
        use_single_model=False,
    )


def _load_sections(sections_path: Path | None, markdown_path: Path | None) -> tuple[list[PaperSection], str]:
    if sections_path is not None:
        payload = json.loads(sections_path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("sections JSON must contain a list of section objects")
        try:
            sections = [PaperSection.model_validate(item) for item in payload]
        except ValidationError as exc:
            raise ValueError(
                "sections JSON must use the OSA PaperSection schema: "
                "section_id, name, text, heading_meta.raw, heading_meta.level"
            ) from exc
        return sections, str(sections_path)

    if markdown_path is None:
        raise ValueError("provide --sections or --markdown")
    sections = MarkdownSectionParser().parse(markdown_path.read_text(encoding="utf-8"))
    return sections, str(markdown_path)


def _serialize_result(result: Any, output_format: str, *, include_debug: bool) -> Any:
    if output_format == "legacy":
        return result.to_legacy_dict(include_debug=include_debug)
    if output_format == "claims-only":
        return [claim.model_dump(mode="json") for claim in result.claims]
    return result.model_dump(mode="json")


async def _extract(args: argparse.Namespace) -> int:
    setup_logging("paper_claims_from_parsed", str(Path.cwd() / "logs"))
    sections, input_source = _load_sections(args.sections, args.markdown)
    source = str(args.source or input_source)

    config = ConfigManager(_build_config_args(args))
    settings = config.get_model_settings("paper_claims")
    handler = ModelHandlerFactory.build(settings)
    reset_provenance = getattr(handler, "reset_model_provenance", None)
    if callable(reset_provenance):
        reset_provenance()

    logger.info("Parsed-input claim extraction started: sections=%s; source=%s", len(sections), source)
    result = await ClaimExtractor(
        handler,
        max_retries=args.extraction_retries,
        dedup_batch_size=args.dedup_batch_size,
        show_progress=args.show_progress,
    ).extract(sections, source=source, model=settings.model)

    provenance = model_provenance(handler, configured=settings.model)
    result.meta.configured_model = provenance.configured
    result.meta.models_used = provenance.used or ([result.meta.model] if result.meta.model else [])
    if result.meta.models_used:
        result.meta.model = result.meta.models_used[-1]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            _serialize_result(result, args.format, include_debug=args.include_debug),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info(
        "Parsed-input claim extraction completed: selected_sections=%s; final_claims=%s; output=%s",
        len(result.selected_section_ids),
        len(result.claims),
        args.output,
    )
    print(f"Wrote {len(result.claims)} claims to {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run paper-claim extraction from an existing sections.json or paper.md file."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--sections", type=Path, help="OSA PaperSection JSON emitted by the section parser.")
    source.add_argument("--markdown", type=Path, help="Parsed paper Markdown to sectionize before claim extraction.")
    parser.add_argument("--source", type=Path, help="Optional source path to store in output metadata.")
    parser.add_argument("--output", type=Path, default=Path("claims.json"))
    parser.add_argument("--format", choices=["typed", "legacy", "claims-only"], default="typed")
    parser.add_argument("--include-debug", action="store_true")
    parser.add_argument("--config-file", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--api", default=None)
    parser.add_argument("--base-url", dest="base_url", default=None)
    parser.add_argument("--temperature", type=float, default=None)
    parser.add_argument("--max-tokens", dest="max_tokens", type=int, default=None)
    parser.add_argument("--context-window", dest="context_window", type=int, default=None)
    parser.add_argument("--top-p", dest="top_p", type=float, default=None)
    parser.add_argument("--extraction-retries", type=int, default=5)
    parser.add_argument("--dedup-batch-size", type=int, default=50)
    parser.add_argument("--show-progress", action=argparse.BooleanOptionalAction, default=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(_extract(args))


if __name__ == "__main__":
    raise SystemExit(main())
