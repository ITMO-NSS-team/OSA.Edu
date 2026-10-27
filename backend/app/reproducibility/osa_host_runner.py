from __future__ import annotations

import runpy
import sys

from ..llm.host_llm import patch_osa_model_handler


def main() -> int:
    patch_osa_model_handler()
    from .osa_edu_runner import prepare_arguments

    sys.argv = ["osa_tool.run", *prepare_arguments(sys.argv[1:])]
    runpy.run_module("osa_tool.run", run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
