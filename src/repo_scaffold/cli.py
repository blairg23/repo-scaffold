from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .generator import (
    SUPPORTED_LICENSE,
    ScaffoldConfig,
    default_output_path,
    generate_scaffold,
    parse_language_csv,
    remove_tree,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="repo-scaffold",
        description="Generate an OS-agnostic GitHub repo scaffold for Go/Python/React projects.",
    )
    parser.add_argument("--name", required=True, help="Repository name")
    parser.add_argument(
        "--languages",
        required=True,
        help="Comma-separated language list: go, python, react",
    )
    parser.add_argument("--owner", help="GitHub owner (user or org)")
    parser.add_argument(
        "--license",
        dest="license_id",
        default=SUPPORTED_LICENSE,
        choices=[SUPPORTED_LICENSE],
        help="License identifier (only apache-2.0 is currently supported)",
    )
    parser.add_argument(
        "--out",
        help="Output path for generated repository (default: ./out/<name>)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output directory if it already exists and is not empty",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> ScaffoldConfig:
    parser = build_parser()
    ns = parser.parse_args(argv)

    try:
        languages = parse_language_csv(ns.languages)
    except ValueError as exc:
        parser.error(str(exc))

    out_dir = Path(ns.out) if ns.out else default_output_path(ns.name)

    return ScaffoldConfig(
        name=ns.name,
        languages=languages,
        owner=ns.owner,
        license_id=ns.license_id,
        out_dir=out_dir,
        overwrite=ns.overwrite,
    )


def _output_conflict(path: Path) -> bool:
    if not path.exists():
        return False
    if not path.is_dir():
        return True
    return any(path.iterdir())


def _confirm_overwrite(path: Path) -> bool:
    answer = input(
        f"Output directory '{path}' already exists and is not empty. Overwrite? [y/N]: "
    ).strip()
    return answer.lower() in {"y", "yes"}


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)

    if _output_conflict(config.out_dir):
        if not config.out_dir.is_dir():
            print(
                f"Error: output path '{config.out_dir}' exists and is not a directory.",
                file=sys.stderr,
            )
            return 2

        if not config.overwrite:
            if not sys.stdin.isatty():
                print(
                    f"Error: output directory '{config.out_dir}' already exists and is not empty. "
                    "Use --overwrite to replace it.",
                    file=sys.stderr,
                )
                return 2
            if not _confirm_overwrite(config.out_dir):
                print("Aborted.")
                return 2

        remove_tree(config.out_dir)

    generate_scaffold(config)
    print(f"Scaffold generated at: {config.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
