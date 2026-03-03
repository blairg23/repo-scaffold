from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

import pytest

from repo_scaffold.cli import main, parse_args


def test_parse_args_defaults() -> None:
    cfg = parse_args(["--name", "demo", "--languages", "go,python"])

    assert cfg.name == "demo"
    assert cfg.languages == ("go", "python")
    assert cfg.owner is None
    assert cfg.license_id == "apache-2.0"
    assert cfg.out_dir == Path("out") / "demo"


def test_parse_args_custom_values() -> None:
    cfg = parse_args(
        [
            "--name",
            "demo",
            "--languages",
            "react,go,react",
            "--owner",
            "acme",
            "--out",
            "/tmp/generated",
        ]
    )

    # language order is canonical and de-duplicated
    assert cfg.languages == ("go", "react")
    assert cfg.owner == "acme"
    assert cfg.out_dir == Path("/tmp/generated")


def test_parse_args_rejects_unknown_language() -> None:
    with pytest.raises(SystemExit) as exc:
        parse_args(["--name", "demo", "--languages", "go,ruby"])

    assert exc.value.code == 2


def test_parse_args_accepts_overwrite_flag() -> None:
    cfg = parse_args(["--name", "demo", "--languages", "go", "--overwrite"])
    assert cfg.overwrite is True


def test_main_requires_overwrite_flag_in_non_tty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out_dir = tmp_path / "demo"
    out_dir.mkdir(parents=True)
    (out_dir / "existing.txt").write_text("keep", encoding="utf-8")

    monkeypatch.setattr("repo_scaffold.cli.sys.stdin", SimpleNamespace(isatty=lambda: False))

    rc = main(["--name", "demo", "--languages", "go", "--out", str(out_dir)])

    assert rc == 2
    assert (out_dir / "existing.txt").exists()


def test_main_prompts_and_overwrites_when_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_dir = tmp_path / "demo"
    out_dir.mkdir(parents=True)
    (out_dir / "existing.txt").write_text("old", encoding="utf-8")

    monkeypatch.setattr("repo_scaffold.cli.sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda _prompt: "y")

    rc = main(["--name", "demo", "--languages", "go", "--owner", "acme", "--out", str(out_dir)])

    assert rc == 0
    assert not (out_dir / "existing.txt").exists()
    assert (out_dir / "go.mod").exists()
