"""Verify paper claims against repository code with local snapshot preparation."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from osa_tool.tools.focused_cli import configure_focused_tool_logging
from osa_tool.tools.paper_analysis.cli import build_parser as build_paper_analysis_parser
from osa_tool.tools.paper_analysis.cli import run_paper_analysis
from osa_tool.utils.logger import logger

DATASET_COMMIT_AUTHOR = "OSA Dataset"
DATASET_COMMIT_EMAIL = "osa-dataset@example.invalid"
DATASET_COMMIT_MESSAGE = "Dataset repository snapshot"


def build_parser() -> argparse.ArgumentParser:
    """Build the wrapper parser while reusing the paper-analysis option contract."""
    parser = build_paper_analysis_parser()
    parser.description = (
        "Verify paper claims against repository code. Local source snapshots without .git are initialized "
        "before delegating to paper_analysis."
    )
    return parser


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Enforce the claims-code-verification contract."""
    if not args.repository:
        parser.error("--repository is required")
    if args.paper is not None:
        parser.error("claims_code_verificaiton accepts --claims-json only; use paper_analysis for --paper")
    if args.claims_json is None:
        parser.error("--claims-json is required")
    if args.paper_output_dir is None:
        parser.error("--output-dir is required")


def _resolve_path_args(args: argparse.Namespace) -> None:
    """Resolve local file arguments before any downstream path handling."""
    args.claims_json = args.claims_json.expanduser().resolve()
    args.paper_output_dir = args.paper_output_dir.expanduser().resolve()

    config_file = getattr(args, "config_file", None)
    if config_file:
        args.config_file = str(Path(config_file).expanduser().resolve())

    repository_path = Path(args.repository).expanduser()
    if repository_path.is_dir():
        args.repository = str(repository_path.resolve())


def _run_git(repo_path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a git command in *repo_path* and include stderr in failures."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=repo_path,
            check=check,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable was not found; cannot prepare a local repository snapshot") from exc
    except subprocess.CalledProcessError as exc:
        command = " ".join(["git", *args])
        details = (exc.stderr or exc.stdout or "").strip()
        if details:
            raise RuntimeError(f"{command} failed in {repo_path}: {details}") from exc
        raise RuntimeError(f"{command} failed in {repo_path}") from exc


def _has_commit(repo_path: Path) -> bool:
    result = _run_git(repo_path, "rev-parse", "--verify", "HEAD", check=False)
    return result.returncode == 0


def _ensure_dataset_commit(repo_path: Path) -> None:
    _run_git(repo_path, "add", "-A")
    _run_git(
        repo_path,
        "-c",
        f"user.name={DATASET_COMMIT_AUTHOR}",
        "-c",
        f"user.email={DATASET_COMMIT_EMAIL}",
        "commit",
        "--allow-empty",
        "-m",
        DATASET_COMMIT_MESSAGE,
    )


def prepare_local_repository(repository: str) -> None:
    """Initialize local non-Git snapshots enough for OSA's local repository bootstrap."""
    repo_path = Path(repository).expanduser()
    if not repo_path.is_dir():
        return

    repo_path = repo_path.resolve()
    if not (repo_path / ".git").exists():
        _run_git(repo_path, "init")
        print(f"Initialized local Git repository at {repo_path}", file=sys.stderr)

    if not _has_commit(repo_path):
        _ensure_dataset_commit(repo_path)
        print(f"Created dummy dataset commit in {repo_path}", file=sys.stderr)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    validate_args(parser, args)
    _resolve_path_args(args)
    if args.include_repository_quality is None:
        args.include_repository_quality = False

    configure_focused_tool_logging(str(args.repository))
    try:
        prepare_local_repository(str(args.repository))
        result = run_paper_analysis(args)
    except Exception as exc:
        logger.exception("Claims code verification failed: %s", exc)
        return 1

    print(result.artifacts.json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
