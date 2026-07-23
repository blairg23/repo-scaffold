"""Tests for project_config: load, save, parse, defaults, wizard."""

from __future__ import annotations

from pathlib import Path

from repo_scaffold.project_config import (
    CONFIG_FILENAME,
    DEFAULT_WORKFLOW_MAPPINGS,
    ProjectConfig,
    StatusOption,
    default_config,
    find_config_file,
    load_config,
    local_dir_matches_repo,
    prompt_config,
    resolve_languages,
    resolve_languages_for_repo,
    save_config,
)


def test_default_config_has_seven_statuses() -> None:
    cfg = default_config()
    assert len(cfg.statuses) == 7
    names = [s.name for s in cfg.statuses]
    assert "Todo" in names
    assert "In Progress" in names
    assert "Done" in names


def test_default_config_has_five_workflow_mappings() -> None:
    cfg = default_config()
    for key in DEFAULT_WORKFLOW_MAPPINGS:
        assert key in cfg.workflows


def test_save_and_load_round_trips(tmp_path: Path) -> None:
    cfg = default_config()
    path = tmp_path / CONFIG_FILENAME
    save_config(cfg, path)
    loaded = load_config(tmp_path)
    assert [s.name for s in loaded.statuses] == [s.name for s in cfg.statuses]
    assert [s.color for s in loaded.statuses] == [s.color for s in cfg.statuses]
    assert loaded.workflows == cfg.workflows


def test_save_and_load_custom_config(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        statuses=[
            StatusOption("Open", "BLUE"),
            StatusOption("Closed", "GREEN"),
        ],
        workflows={
            "branch_created": "Open",
            "pr_merged": "Closed",
            "pr_opened": "Open",
            "issue_closed": "Closed",
            "issue_reopened": "Open",
        },
    )
    path = tmp_path / CONFIG_FILENAME
    save_config(cfg, path)
    loaded = load_config(tmp_path)
    assert loaded.statuses[0].name == "Open"
    assert loaded.statuses[1].name == "Closed"
    assert loaded.workflows["branch_created"] == "Open"
    assert loaded.workflows["pr_merged"] == "Closed"


def test_load_config_returns_defaults_when_file_absent(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    defaults = default_config()
    assert [s.name for s in cfg.statuses] == [s.name for s in defaults.statuses]


def test_find_config_file_walks_up(tmp_path: Path) -> None:
    config = tmp_path / CONFIG_FILENAME
    config.write_text("project:\n  statuses: []\n", encoding="utf-8")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    found = find_config_file(nested)
    assert found == config


def test_find_config_file_returns_none_when_not_found(tmp_path: Path) -> None:
    assert find_config_file(tmp_path) is None


def test_load_config_falls_back_to_defaults_on_corrupt_file(tmp_path: Path) -> None:
    bad = tmp_path / CONFIG_FILENAME
    bad.write_text("not: valid: yaml: at: all: [[[\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    defaults = default_config()
    assert len(cfg.statuses) == len(defaults.statuses)


def test_status_names_with_quotes_round_trip(tmp_path: Path) -> None:
    cfg = ProjectConfig(
        statuses=[StatusOption("Won't Do", "GRAY")],
        workflows=dict(DEFAULT_WORKFLOW_MAPPINGS),
    )
    path = tmp_path / CONFIG_FILENAME
    save_config(cfg, path)
    loaded = load_config(tmp_path)
    assert loaded.statuses[0].name == "Won't Do"


def test_prompt_config_accepts_defaults(tmp_path: Path) -> None:
    cfg = default_config()
    prompts: list[str] = []

    def _prompt(msg: str) -> str:
        prompts.append(msg)
        return ""

    result = prompt_config(cfg, prompt_fn=_prompt, out=lambda _: None)
    assert [s.name for s in result.statuses] == [s.name for s in cfg.statuses]
    assert result.workflows == cfg.workflows


def test_prompt_config_overrides_status(tmp_path: Path) -> None:
    cfg = default_config()
    answers = iter(
        ["NewName:RED"] + [""] * (len(cfg.statuses) - 1 + len(cfg.workflows))
    )

    def _prompt(_msg: str) -> str:
        return next(answers, "")

    result = prompt_config(cfg, prompt_fn=_prompt, out=lambda _: None)
    assert result.statuses[0].name == "NewName"
    assert result.statuses[0].color == "RED"


def test_prompt_config_overrides_workflow(tmp_path: Path) -> None:
    cfg = default_config()
    n_statuses = len(cfg.statuses)
    n_workflows = len(cfg.workflows)
    answers = iter([""] * n_statuses + ["Custom Status"] + [""] * (n_workflows - 1))

    def _prompt(_msg: str) -> str:
        return next(answers, "")

    result = prompt_config(cfg, prompt_fn=_prompt, out=lambda _: None)
    first_workflow_key = next(iter(cfg.workflows))
    assert result.workflows[first_workflow_key] == "Custom Status"


def test_default_config_has_no_languages() -> None:
    cfg = default_config()
    assert cfg.languages == []


def test_save_and_load_config_roundtrips_languages(tmp_path: Path) -> None:
    cfg = default_config()
    cfg.languages = ["python", "react"]
    save_config(cfg, tmp_path / CONFIG_FILENAME)
    reloaded = load_config(tmp_path)
    assert reloaded.languages == ["python", "react"]


def test_save_config_omits_languages_section_when_empty(tmp_path: Path) -> None:
    cfg = default_config()
    save_config(cfg, tmp_path / CONFIG_FILENAME)
    text = (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert "languages:" not in text


def test_resolve_languages_prefers_declared_config(tmp_path: Path) -> None:
    cfg = default_config()
    cfg.languages = ["go"]
    save_config(cfg, tmp_path / CONFIG_FILENAME)
    assert resolve_languages(tmp_path) == ["go"]


def test_resolve_languages_falls_back_to_detection(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    assert resolve_languages(tmp_path) == ["python"]


def _write_git_config(repo_dir: Path, remote_url: str) -> None:
    git_dir = repo_dir / ".git"
    git_dir.mkdir(parents=True, exist_ok=True)
    (git_dir / "config").write_text(
        f'[remote "origin"]\n\turl = {remote_url}\n', encoding="utf-8"
    )


def test_local_dir_matches_repo_no_git_config(tmp_path: Path) -> None:
    assert local_dir_matches_repo(tmp_path, "acme/widgets") is False


def test_local_dir_matches_repo_matching_remote(tmp_path: Path) -> None:
    _write_git_config(tmp_path, "https://github.com/acme/widgets.git")
    assert local_dir_matches_repo(tmp_path, "acme/widgets") is True


def test_local_dir_matches_repo_non_matching_remote(tmp_path: Path) -> None:
    _write_git_config(tmp_path, "https://github.com/other/unrelated.git")
    assert local_dir_matches_repo(tmp_path, "acme/widgets") is False


def test_local_dir_matches_repo_unreadable_config(tmp_path: Path) -> None:
    # A directory where a file is expected makes read_text() raise OSError.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").mkdir()
    assert local_dir_matches_repo(tmp_path, "acme/widgets") is False


def test_resolve_languages_for_repo_prefers_declared_config(tmp_path: Path) -> None:
    cfg = default_config()
    cfg.languages = ["go"]
    save_config(cfg, tmp_path / CONFIG_FILENAME)
    assert resolve_languages_for_repo(tmp_path, "acme/widgets") == ["go"]


def test_resolve_languages_for_repo_warns_and_returns_empty_for_unrelated_dir(
    tmp_path: Path,
) -> None:
    warnings: list[str] = []
    result = resolve_languages_for_repo(tmp_path, "acme/widgets", warn=warnings.append)
    assert result == []
    assert len(warnings) == 1
    assert "acme/widgets" in warnings[0]


def test_resolve_languages_for_repo_silent_without_warn_callback(
    tmp_path: Path,
) -> None:
    assert resolve_languages_for_repo(tmp_path, "acme/widgets") == []


def test_resolve_languages_for_repo_detects_from_matching_clone(
    tmp_path: Path,
) -> None:
    _write_git_config(tmp_path, "https://github.com/acme/widgets.git")
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n", encoding="utf-8")
    assert resolve_languages_for_repo(tmp_path, "acme/widgets") == ["python"]
