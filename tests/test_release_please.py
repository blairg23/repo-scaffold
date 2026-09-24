"""Tests for release-please and PR-convention scaffolding."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from repo_scaffold import create_ops
from repo_scaffold.generator import (
    DEFAULT_RELEASE_VERSION,
    TEMPLATE_ROOT,
    ScaffoldConfig,
    ScaffoldFile,
    build_ci_files,
    build_scaffold_files,
    current_release_version,
    release_please_release_type,
)
from repo_scaffold.overwrite_policy import OverwritePolicy, apply_files

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = TEMPLATE_ROOT / "github" / "workflows"

RELEASE_FILES = (
    ".github/workflows/release-please.yml",
    ".github/workflows/pr-conventions.yml",
    "release-please-config.json",
    ".release-please-manifest.json",
)


def _scaffold(tmp_path: Path, languages: tuple[str, ...]) -> dict[str, ScaffoldFile]:
    cfg = ScaffoldConfig(
        name="demo-app",
        languages=languages,
        owner="blairg23",
        license_id="apache-2.0",
        out_dir=tmp_path,
    )
    return {
        f.path.relative_to(tmp_path).as_posix(): f for f in build_scaffold_files(cfg)
    }


def _config(files: dict[str, ScaffoldFile]) -> dict:
    return json.loads(files["release-please-config.json"].content)


# --------------------------------------------------------------- presence


@pytest.mark.parametrize(
    "languages", [("python",), ("react",), ("go",), ("gin",), ("python", "react")]
)
def test_init_scaffolds_every_release_artefact(
    tmp_path: Path, languages: tuple[str, ...]
) -> None:
    files = _scaffold(tmp_path, languages)

    for rel in RELEASE_FILES:
        assert rel in files, rel


def test_apply_ci_includes_every_release_artefact(tmp_path: Path) -> None:
    files = build_ci_files(tmp_path, languages=("python",))
    rels = {f.path.relative_to(tmp_path).as_posix() for f in files}

    assert set(RELEASE_FILES) <= rels


# ----------------------------------------------------------- release type


def test_python_repo_uses_python_release_type(tmp_path: Path) -> None:
    assert release_please_release_type(("python",), tmp_path) == "python"


def test_python_wins_over_a_root_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    assert release_please_release_type(("python", "react"), tmp_path) == "python"


def test_root_package_json_uses_node_release_type(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")

    assert release_please_release_type(("react",), tmp_path) == "node"


def test_web_only_frontend_falls_back_to_simple(tmp_path: Path) -> None:
    """repo-scaffold puts React in web/; node expects package.json at the root."""
    assert release_please_release_type(("react",), tmp_path) == "simple"


@pytest.mark.parametrize("languages", [("go",), ("gin",), ()])
def test_everything_else_falls_back_to_simple(
    tmp_path: Path, languages: tuple[str, ...]
) -> None:
    assert release_please_release_type(languages, tmp_path) == "simple"


def test_react_bumps_web_package_json_through_extra_files(tmp_path: Path) -> None:
    package = _config(_scaffold(tmp_path, ("react",)))["packages"]["."]

    assert package["extra-files"] == [
        {"type": "json", "path": "web/package.json", "jsonpath": "$.version"}
    ]


def test_repos_without_a_frontend_have_no_extra_files(tmp_path: Path) -> None:
    package = _config(_scaffold(tmp_path, ("python",)))["packages"]["."]

    assert "extra-files" not in package


# ---------------------------------------------------------------- version


def test_version_defaults_for_a_fresh_scaffold(tmp_path: Path) -> None:
    assert current_release_version(tmp_path) == DEFAULT_RELEASE_VERSION


def test_version_is_read_from_pyproject_project_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "2.3.1"\n', encoding="utf-8"
    )

    assert current_release_version(tmp_path) == "2.3.1"


def test_version_is_read_from_poetry_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[tool.poetry]\nname = "x"\nversion = "0.9.4"  # bumped by hand\n',
        encoding="utf-8",
    )

    assert current_release_version(tmp_path) == "0.9.4"


def test_version_ignores_versions_outside_project_tables(tmp_path: Path) -> None:
    """A dependency's version pin must not be mistaken for the project version."""
    (tmp_path / "pyproject.toml").write_text(
        '[tool.black]\nversion = "9.9.9"\n', encoding="utf-8"
    )

    assert current_release_version(tmp_path) == DEFAULT_RELEASE_VERSION


def test_version_is_read_from_root_package_json(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"version": "3.0.2"}', encoding="utf-8")

    assert current_release_version(tmp_path) == "3.0.2"


def test_version_is_read_from_web_package_json(tmp_path: Path) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(
        '{"version": "1.1.0"}', encoding="utf-8"
    )

    assert current_release_version(tmp_path) == "1.1.0"


def test_non_semver_version_falls_back_to_default(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"version": "latest"}', encoding="utf-8")

    assert current_release_version(tmp_path) == DEFAULT_RELEASE_VERSION


def test_malformed_package_json_falls_back_to_default(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{not json", encoding="utf-8")

    assert current_release_version(tmp_path) == DEFAULT_RELEASE_VERSION


def test_manifest_is_seeded_from_the_detected_version(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "4.2.0"\n', encoding="utf-8"
    )

    manifest = _scaffold(tmp_path, ("python",))[".release-please-manifest.json"]

    assert json.loads(manifest.content) == {".": "4.2.0"}


# ------------------------------------------------ manifest is never reset


def test_manifest_is_marked_create_only(tmp_path: Path) -> None:
    files = _scaffold(tmp_path, ("python",))

    assert files[".release-please-manifest.json"].create_only
    assert not files["release-please-config.json"].create_only


@pytest.mark.parametrize(
    "policy",
    [OverwritePolicy(yes=True), OverwritePolicy(force=True)],
    ids=["--yes", "--force"],
)
def test_existing_manifest_survives_reapply(
    tmp_path: Path, policy: OverwritePolicy
) -> None:
    """Re-running `apply ci` must never roll a released repo back to 0.1.0."""
    manifest = tmp_path / ".release-please-manifest.json"
    manifest.write_text('{".": "1.4.0"}\n', encoding="utf-8")
    files = build_ci_files(tmp_path, languages=("python",))

    summary = apply_files(files, policy, out=lambda _: None, err=lambda _: None)

    assert json.loads(manifest.read_text(encoding="utf-8")) == {".": "1.4.0"}
    assert summary.failures == 0


def test_missing_manifest_is_created(tmp_path: Path) -> None:
    files = build_ci_files(tmp_path, languages=("python",))

    apply_files(files, OverwritePolicy(yes=True), out=lambda _: None)

    assert (tmp_path / ".release-please-manifest.json").is_file()


# ------------------------------------------------------------------ config


def test_config_tags_plain_versions_for_a_single_root_package(tmp_path: Path) -> None:
    config = _config(_scaffold(tmp_path, ("python",)))

    assert list(config["packages"]) == ["."]
    assert config["include-component-in-tag"] is False
    assert config["bump-minor-pre-major"] is True


def test_every_ticket_type_gets_a_visible_changelog_section(tmp_path: Path) -> None:
    """Release notes must list every completed ticket (AGENTS.md PR types)."""
    sections = _config(_scaffold(tmp_path, ("python",)))["changelog-sections"]
    visible = {s["type"] for s in sections if not s["hidden"]}

    assert {"feat", "fix", "docs", "chore", "refactor", "test"} <= visible


def test_dependency_bump_types_are_hidden(tmp_path: Path) -> None:
    sections = _config(_scaffold(tmp_path, ("python",)))["changelog-sections"]
    hidden = {s["type"] for s in sections if s["hidden"]}

    assert {"build", "ci"} <= hidden


def test_pr_title_types_match_changelog_sections(tmp_path: Path) -> None:
    """A type allowed by the title check but absent from the changelog would
    silently drop that PR from the release notes."""
    sections = _config(_scaffold(tmp_path, ("python",)))["changelog-sections"]
    workflow = (WORKFLOWS / "pr-conventions.yml").read_text(encoding="utf-8")
    block = workflow.split("types: |", 1)[1].split("requireScope", 1)[0]
    title_types = {line.strip() for line in block.splitlines() if line.strip()}

    assert title_types == {s["type"] for s in sections}


# ---------------------------------------------------------------- workflows


@pytest.mark.parametrize("name", ["release-please.yml", "pr-conventions.yml"])
def test_every_action_is_pinned_to_a_full_sha(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    uses = re.findall(r"uses:\s*(\S+)(.*)", text)

    assert uses
    for ref, trailing in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert re.search(
            r"#\s*v\d+\.\d+\.\d+", trailing
        ), f"{ref} lacks a version comment"


def test_release_workflow_defaults_to_github_token() -> None:
    text = (WORKFLOWS / "release-please.yml").read_text(encoding="utf-8")

    assert "steps.app-token.outputs.token || secrets.GITHUB_TOKEN" in text


def test_release_workflow_app_token_is_optional() -> None:
    text = (WORKFLOWS / "release-please.yml").read_text(encoding="utf-8")

    assert "if: ${{ vars.RELEASE_PLEASE_CLIENT_ID != '' }}" in text
    # v3 of create-github-app-token deprecated app-id in favour of client-id.
    assert "client-id:" in text
    assert "app-id:" not in text


def test_release_workflow_documents_the_github_token_limitation() -> None:
    text = (WORKFLOWS / "release-please.yml").read_text(encoding="utf-8")

    assert "does not\n# trigger other workflows" in text


def test_ticket_link_exempts_every_sync_branch_create_ops_uses() -> None:
    """Renaming a sync branch must not silently start failing its own PRs."""
    text = (WORKFLOWS / "pr-conventions.yml").read_text(encoding="utf-8")

    for branch in (
        create_ops._TEMPLATES_SYNC_BRANCH,
        create_ops._CONFIGS_SYNC_BRANCH,
        "chore/add-dependabot-yml",
    ):
        assert f'"{branch}"' in text, branch
    assert '"release-please--"' in text
    assert '"dependabot[bot]"' in text


def test_validate_pr_exempts_release_please_in_both_copies() -> None:
    template = (WORKFLOWS / "validate-pr.yml").read_text(encoding="utf-8")
    own = (REPO_ROOT / ".github" / "workflows" / "validate-pr.yml").read_text(
        encoding="utf-8"
    )

    assert template == own
    assert 'head.startsWith("release-please--")' in template


# ------------------------------------------------------------------ secrets


_SECRET_PATTERNS = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


@pytest.mark.parametrize("languages", [("python",), ("react",), ("go",)])
def test_no_secret_values_in_generated_files(
    tmp_path: Path, languages: tuple[str, ...]
) -> None:
    for rel in RELEASE_FILES:
        assert not _SECRET_PATTERNS.search(_scaffold(tmp_path, languages)[rel].content)


def test_secrets_are_only_ever_referenced_never_inlined() -> None:
    for name in ("release-please.yml", "pr-conventions.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "secrets." in line and not line.lstrip().startswith("#"):
                assert re.search(r"\$\{\{[^}]*secrets\.[A-Z_]+[^}]*\}\}", line), line


# --------------------------------------------------------------- dependabot


def test_scaffolded_dependabot_uses_conventional_prefixes(tmp_path: Path) -> None:
    text = _scaffold(tmp_path, ("python",))[".github/dependabot.yml"].content

    assert 'prefix: "ci"' in text
    assert 'prefix: "build"' in text
    assert text.count('include: "scope"') == text.count("package-ecosystem")


def test_apply_rules_dependabot_uses_conventional_prefixes() -> None:
    text = create_ops._minimal_dependabot_yml(["python"])

    assert 'prefix: "ci"' in text
    assert 'prefix: "build"' in text
    assert text.count('include: "scope"') == text.count("package-ecosystem")


# ------------------------------------------- required status checks (#322)


def _required(payload: str) -> list[str]:
    ruleset = json.loads(payload)
    rule = next(r for r in ruleset["rules"] if r["type"] == "required_status_checks")
    return [c["context"] for c in rule["parameters"]["required_status_checks"]]


def _status_drifts(ruleset: dict, *, include: bool) -> list[str]:
    drifts = create_ops._compare_ruleset_against_baseline(
        [ruleset],
        default_branch="main",
        languages=["python"],
        include_pr_conventions=include,
    )
    return [d for d in drifts if "required_status_checks" in d]


def test_convention_checks_are_not_required_without_the_workflow() -> None:
    """A required context no workflow reports would block every PR forever."""
    contexts = _required(create_ops._default_branch_ruleset_payload(["python"]))

    assert "conventional-title" not in contexts
    assert "ticket-link" not in contexts


def test_convention_checks_are_required_with_the_workflow() -> None:
    contexts = _required(
        create_ops._default_branch_ruleset_payload(
            ["python"], include_pr_conventions=True
        )
    )

    assert {"conventional-title", "ticket-link"} <= set(contexts)
    assert {"check-sop", "validate-pr"} <= set(contexts)


def test_has_pr_conventions_follows_the_workflow_file(tmp_path: Path) -> None:
    assert not create_ops._has_pr_conventions(tmp_path)

    workflow = tmp_path / ".github" / "workflows" / "pr-conventions.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: PR conventions\n", encoding="utf-8")

    assert create_ops._has_pr_conventions(tmp_path)


def test_payload_and_drift_check_agree() -> None:
    """Both paths share one context list, so a ruleset we apply is never drift."""
    ruleset = json.loads(
        create_ops._default_branch_ruleset_payload(
            ["python"], include_pr_conventions=True
        )
    )

    assert _status_drifts(ruleset, include=True) == []


def test_drift_flags_convention_checks_missing_from_an_opted_in_repo() -> None:
    ruleset = json.loads(create_ops._default_branch_ruleset_payload(["python"]))

    drifts = _status_drifts(ruleset, include=True)

    assert drifts
    assert "conventional-title" in drifts[0]
    assert "ticket-link" in drifts[0]


def test_repo_without_the_workflow_reports_no_convention_drift() -> None:
    ruleset = json.loads(create_ops._default_branch_ruleset_payload(["python"]))

    assert _status_drifts(ruleset, include=False) == []


def test_required_contexts_match_the_workflow_job_names() -> None:
    """Renaming a job must fail here, not silently unrequire the check."""
    text = (WORKFLOWS / "pr-conventions.yml").read_text(encoding="utf-8")
    job_names = set(re.findall(r"^    name: (\S+)$", text, flags=re.MULTILINE))

    assert set(create_ops._PR_CONVENTION_CONTEXTS) == job_names


def test_applying_the_ruleset_requires_convention_checks_when_opted_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real apply path, reading the workflow from the checkout on disk."""
    workflow = tmp_path / ".github" / "workflows" / "pr-conventions.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: PR conventions\n", encoding="utf-8")
    sent: list[str] = []

    monkeypatch.setattr(create_ops, "_list_repo_rulesets", lambda **_: [])

    def fake_api(**kwargs):  # type: ignore[no-untyped-def]
        sent.append(kwargs.get("stdin_text") or "")
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr=""
        )

    monkeypatch.setattr(create_ops, "_api", fake_api)

    create_ops._sync_default_branch_ruleset(
        repo_dir=tmp_path,
        env={},
        repo="o/r",
        out=lambda _: None,
        languages=["python"],
    )

    assert sent
    assert {"conventional-title", "ticket-link"} <= set(_required(sent[0]))


def test_conventional_title_does_not_run_for_dependabot() -> None:
    """Opted-in repos keep their own dependabot.yml, which may not produce
    Conventional Commits titles; a skipped job still satisfies the check."""
    text = (WORKFLOWS / "pr-conventions.yml").read_text(encoding="utf-8")
    job = text.split("  conventional-title:", 1)[1].split("\n  ticket-link:", 1)[0]

    assert "if: ${{ github.event.pull_request.user.login != 'dependabot[bot]' }}" in job
