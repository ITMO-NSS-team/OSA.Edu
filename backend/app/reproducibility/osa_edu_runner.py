"""Own PDF parsing and prompts in Edu; delegate the generic workflow to OSA."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

from .runner import PROGRESS_PREFIX, use_host_llm
from .thesis_parser import PROMPTS_DIR, write_thesis_sections


def prepare_arguments(arguments: list[str]) -> list[str]:
    arguments = list(arguments)
    if "--paper" in arguments:
        index = arguments.index("--paper")
        paper = Path(arguments[index + 1])
        output = Path(arguments[arguments.index("--paper-output-dir") + 1])
        print(PROGRESS_PREFIX + json.dumps({
            "percent": 12, "message": "OSA.Edu: разбираем текстовый слой и структуру PDF."
        }, ensure_ascii=False), flush=True)
        sections = write_thesis_sections(paper, output)
        arguments[index:index + 2] = ["--sections-json", str(sections)]
        arguments.extend(["--paper-claims-prompts-dir", str(PROMPTS_DIR)])
    return arguments


def main() -> int:
    arguments = prepare_arguments(sys.argv[1:])
    if use_host_llm():
        from ..llm.host_llm import patch_osa_model_handler

        patch_osa_model_handler()
    sys.argv = ["osa_tool.run", *arguments]
    runpy.run_module("osa_tool.run", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
