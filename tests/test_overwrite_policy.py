from __future__ import annotations

import sys
from pathlib import Path

from repo_scaffold.generator import ScaffoldFile
from repo_scaffold.overwrite_policy import OverwritePolicy, apply_files


def test_apply_files_handles_create_skip_prompt_force_backup_and_failures(
    tmp_path: Path,
) -> None:
    create_path = tmp_path / "new.txt"
    unchanged_path = tmp_path / "same.txt"
    unchanged_path.write_text("same\n", encoding="utf-8")
    prompted_path = tmp_path / "prompt.txt"
    prompted_path.write_text("old\n", encoding="utf-8")
    forced_path = tmp_path / "forced.sh"
    forced_path.write_text("echo new\n", encoding="utf-8")
    forced_path.chmod(0o755)
    blocked_path = tmp_path / "blocked"
    blocked_path.mkdir()

    files = [
        ScaffoldFile(create_path, "created", executable=False),
        ScaffoldFile(unchanged_path, "same", executable=False),
        ScaffoldFile(prompted_path, "new", executable=False),
        ScaffoldFile(forced_path, "echo new", executable=True),
        ScaffoldFile(blocked_path, "nope", executable=False),
    ]

    prompted_lines: list[str] = []
    prompted_errors: list[str] = []
    prompted = apply_files(
        files,
        OverwritePolicy(),
        prompt=lambda _message: "yes",
        is_tty=True,
        out=prompted_lines.append,
        err=prompted_errors.append,
    )

    assert prompted.created == 1
    assert prompted.overwritten == 1
    assert prompted.skipped == 2
    assert prompted.failures == 1
    assert create_path.read_text(encoding="utf-8") == "created\n"
    assert prompted_path.read_text(encoding="utf-8") == "new\n"
    assert any(
        "Error: destination exists and is not a regular file" in line
        for line in prompted_errors
    )

    forced_lines: list[str] = []
    forced = apply_files(
        [ScaffoldFile(forced_path, "echo newer", executable=True)],
        OverwritePolicy(force=True, backup=True),
        out=forced_lines.append,
    )

    assert forced.created == 0
    assert forced.overwritten == 1
    assert forced.skipped == 0
    assert forced.failures == 0
    assert forced_path.read_text(encoding="utf-8") == "echo newer\n"
    if sys.platform != "win32":
        assert forced_path.stat().st_mode & 0o111
    backups = list(tmp_path.glob("forced.sh.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "echo new\n"
    assert "OVERWRITE" in "\n".join(forced_lines)


def test_apply_files_respects_no_and_dry_run(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("old\n", encoding="utf-8")

    summary_no = apply_files(
        [ScaffoldFile(target, "new", executable=False)],
        OverwritePolicy(no=True),
        is_tty=False,
        out=lambda _line: None,
    )
    assert summary_no.overwritten == 0
    assert summary_no.skipped == 1
    assert target.read_text(encoding="utf-8") == "old\n"

    summary_dry = apply_files(
        [ScaffoldFile(target, "new", executable=False)],
        OverwritePolicy(dry_run=True),
        is_tty=False,
        out=lambda _line: None,
    )
    assert summary_dry.overwritten == 1
    assert target.read_text(encoding="utf-8") == "old\n"
