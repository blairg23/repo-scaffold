"""Config file (.repo-scaffold.yml) for project setup defaults."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

CONFIG_FILENAME = ".repo-scaffold.yml"

DEFAULT_STATUS_OPTIONS: list[dict[str, str]] = [
    {"name": "Todo", "color": "GRAY"},
    {"name": "In Progress", "color": "YELLOW"},
    {"name": "To Review", "color": "BLUE"},
    {"name": "Done", "color": "GREEN"},
    {"name": "Won't Do", "color": "GRAY"},
    {"name": "Duplicate", "color": "BLUE"},
    {"name": "Blocked", "color": "RED"},
]

DEFAULT_WORKFLOW_MAPPINGS: dict[str, str] = {
    "branch_created": "In Progress",
    "pr_opened": "To Review",
    "pr_merged": "Done",
    "issue_closed": "Done",
    "issue_reopened": "Todo",
}

WORKFLOW_LABELS: dict[str, str] = {
    "branch_created": "branch created",
    "pr_opened": "PR opened",
    "pr_merged": "PR merged",
    "issue_closed": "issue closed",
    "issue_reopened": "issue reopened",
}

VALID_COLORS = {"GRAY", "YELLOW", "BLUE", "GREEN", "RED", "PINK", "ORANGE"}


@dataclass
class StatusOption:
    name: str
    color: str


@dataclass
class ProjectConfig:
    statuses: list[StatusOption]
    workflows: dict[str, str]


def default_config() -> ProjectConfig:
    return ProjectConfig(
        statuses=[StatusOption(s["name"], s["color"]) for s in DEFAULT_STATUS_OPTIONS],
        workflows=dict(DEFAULT_WORKFLOW_MAPPINGS),
    )


def find_config_file(start_dir: Path) -> Path | None:
    """Walk up from start_dir looking for .repo-scaffold.yml."""
    current = start_dir.resolve()
    for _ in range(10):
        candidate = current / CONFIG_FILENAME
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def load_config(search_dir: Path) -> ProjectConfig:
    """Return config from .repo-scaffold.yml, falling back to built-in defaults."""
    config_file = find_config_file(search_dir)
    if config_file is None:
        return default_config()
    try:
        return _parse_yaml_config(config_file.read_text(encoding="utf-8"))
    except Exception:
        return default_config()


def save_config(config: ProjectConfig, path: Path) -> None:
    """Write ProjectConfig to a .repo-scaffold.yml file."""
    lines = ["project:", "  statuses:"]
    for s in config.statuses:
        name = s.name.replace('"', '\\"')
        lines.append(f'    - name: "{name}"')
        lines.append(f"      color: {s.color}")
    lines.append("  workflows:")
    for key, value in config.workflows.items():
        v = value.replace('"', '\\"')
        lines.append(f'    {key}: "{v}"')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1].replace('\\"', '"')
    return value


def _parse_yaml_config(text: str) -> ProjectConfig:
    """Parse the specific subset of YAML that save_config writes."""
    statuses: list[StatusOption] = []
    workflows: dict[str, str] = {}
    section: str | None = None
    current: dict[str, str] = {}

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        if indent == 0:
            continue

        if indent == 2:
            key = stripped.rstrip(":")
            if key in ("statuses", "workflows"):
                if current.get("name"):
                    statuses.append(
                        StatusOption(current["name"], current.get("color", "GRAY"))
                    )
                    current = {}
                section = key
            continue

        if section == "statuses":
            if indent == 4 and stripped.startswith("- "):
                if current.get("name"):
                    statuses.append(
                        StatusOption(current["name"], current.get("color", "GRAY"))
                    )
                current = {}
                rest = stripped[2:]
                m = re.match(r"name:\s*(.*)", rest)
                if m:
                    current["name"] = _strip_quotes(m.group(1))
            elif indent in (4, 6) and ":" in stripped:
                k, _, v = stripped.partition(":")
                k = k.strip()
                if k in ("name", "color"):
                    current[k] = _strip_quotes(v)

        elif section == "workflows":
            if indent == 4 and ":" in stripped:
                k, _, v = stripped.partition(":")
                workflows[k.strip()] = _strip_quotes(v)

    if current.get("name"):
        statuses.append(StatusOption(current["name"], current.get("color", "GRAY")))

    defaults = default_config()
    return ProjectConfig(
        statuses=statuses if statuses else defaults.statuses,
        workflows=(
            {**defaults.workflows, **workflows} if workflows else defaults.workflows
        ),
    )


def prompt_config(
    current: ProjectConfig,
    prompt_fn: Callable[[str], str],
    out: Callable[[str], None] = print,
) -> ProjectConfig:
    """Interactive wizard: show current settings, let user override each one."""
    out("")
    out("Statuses (press Enter to keep default, or type name:COLOR to change):")
    out(f"  Colors: {', '.join(sorted(VALID_COLORS))}")
    out("")
    statuses: list[StatusOption] = []
    for s in current.statuses:
        raw = prompt_fn(f"  [{s.name} / {s.color}]: ").strip()
        if not raw:
            statuses.append(s)
        elif ":" in raw:
            parts = raw.split(":", 1)
            statuses.append(StatusOption(parts[0].strip(), parts[1].strip().upper()))
        else:
            statuses.append(StatusOption(raw, s.color))

    out("")
    out("Workflow mappings (press Enter to keep default):")
    out("")
    workflows: dict[str, str] = {}
    for key, default_status in current.workflows.items():
        label = WORKFLOW_LABELS.get(key, key)
        raw = prompt_fn(f"  When {label} -> [{default_status}]: ").strip()
        workflows[key] = raw if raw else default_status

    return ProjectConfig(statuses=statuses, workflows=workflows)
