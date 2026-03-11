from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .generator import ScaffoldFile


@dataclass(frozen=True)
class OverwritePolicy:
    yes: bool = False
    no: bool = False
    force: bool = False
    dry_run: bool = False
    backup: bool = False


@dataclass(frozen=True)
class ApplySummary:
    created: int
    overwritten: int
    skipped: int
    failures: int


def _normalize(content: str) -> str:
    return content.rstrip("\n") + "\n"


def _has_execute_bit(path: Path) -> bool:
    return bool(path.stat().st_mode & 0o111)


def _write_file(path: Path, content: str, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_normalize(content), encoding="utf-8", newline="\n")
    if executable:
        path.chmod(path.stat().st_mode | 0o755)


def _prompt_decision(path: str, prompt: Callable[[str], str]) -> bool:
    answer = prompt(f"Overwrite {path}? [y/N]").strip().lower()
    return answer in {"y", "yes"}


def apply_files(
    files: Iterable[ScaffoldFile],
    policy: OverwritePolicy,
    *,
    prompt: Callable[[str], str] = input,
    is_tty: bool = True,
    out: Callable[[str], None] = print,
    err: Callable[[str], None] | None = None,
) -> ApplySummary:
    emit_err = err if err is not None else (lambda line: print(line, file=sys.stderr))
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")

    created = 0
    overwritten = 0
    skipped = 0
    failures = 0

    for file in sorted(files, key=lambda item: item.path.as_posix()):
        path = file.path
        display = path.as_posix()

        if path.exists() and not path.is_file():
            emit_err(f"Error: destination exists and is not a regular file: {display}")
            failures += 1
            continue

        if not path.exists():
            out(f"CREATE    {display}")
            created += 1
            if not policy.dry_run:
                _write_file(path, file.content, file.executable)
            continue

        desired = _normalize(file.content)
        try:
            current = path.read_text(encoding="utf-8")
            content_changed = current != desired
        except UnicodeDecodeError:
            content_changed = True

        needs_overwrite = content_changed or (
            file.executable and not _has_execute_bit(path)
        )
        if not needs_overwrite:
            out(f"SKIP      {display} (exists)")
            skipped += 1
            continue

        should_overwrite = False
        overwrite_reason = "prompted"
        skipped_reason = "exists"

        if policy.no:
            should_overwrite = False
            skipped_reason = "--no"
        elif policy.force:
            should_overwrite = True
            overwrite_reason = "--force"
        elif policy.yes or policy.dry_run:
            should_overwrite = True
        elif is_tty:
            should_overwrite = _prompt_decision(display, prompt)
        else:
            should_overwrite = False

        if should_overwrite:
            out(f"OVERWRITE {display} ({overwrite_reason})")
            overwritten += 1
            if policy.dry_run:
                continue
            if policy.backup:
                backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
                shutil.copy2(path, backup_path)
            _write_file(path, file.content, file.executable)
        else:
            out(f"SKIP      {display} ({skipped_reason})")
            skipped += 1

    return ApplySummary(
        created=created,
        overwritten=overwritten,
        skipped=skipped,
        failures=failures,
    )
