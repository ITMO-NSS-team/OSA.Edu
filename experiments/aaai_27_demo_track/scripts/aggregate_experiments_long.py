#!/usr/bin/env python3
"""Write aggregate experiment tables with per-thesis appendices."""

from experiments.aaai_27_demo_track.scripts.aggregate_experiments_common import (
    write_outputs,
)


def main() -> None:
    outputs = write_outputs("long", include_appendix=True)
    print("Wrote long aggregate tables:")
    for path in outputs:
        print(f"- {path.name}")


if __name__ == "__main__":
    main()
