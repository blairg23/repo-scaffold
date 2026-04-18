from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict

from .generator import ScaffoldFile

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class BacklogImportSummary:
    source_dir: Path
    output_file: Path
    files_scanned: int
    epics_imported: int
    tickets_imported: int
    synthetic_epics: int
    epics_skipped_existing: int
    tickets_skipped_existing: int


@dataclass(frozen=True)
class _MarkdownIssue:
    path: Path
    relative_parent: Path
    kind: str
    title: str
    body: str
    labels: tuple[str, ...]
    assignees: tuple[str, ...]
    priority: str | None
    key: str | None
    epic_key: str | None


class _TicketPayload(TypedDict):
    title: str
    body: str
    labels: list[str]
    assignees: list[str]
    priority: NotRequired[str]


class _EpicPayload(TypedDict):
    key: str
    title: str
    body: str
    labels: list[str]
    assignees: list[str]
    tickets: list[_TicketPayload]


class _BacklogPayload(TypedDict):
    epics: list[_EpicPayload]


@dataclass(frozen=True)
class _MergeSummary:
    payload: _BacklogPayload
    epics_imported: int
    tickets_imported: int
    synthetic_epics: int
    epics_skipped_existing: int
    tickets_skipped_existing: int


def build_backlog_import_file(
    *,
    source_dir: Path,
    output_file: Path,
) -> tuple[ScaffoldFile, BacklogImportSummary]:
    source_dir = source_dir.resolve()
    output_file = output_file.resolve()

    if not source_dir.exists() or not source_dir.is_dir():
        raise RuntimeError(
            f"Markdown backlog source directory does not exist or is not a directory: {source_dir}"
        )

    markdown_files = sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() == ".md"
    )
    if not markdown_files:
        raise RuntimeError(f"No markdown files found under: {source_dir}")

    parsed = [
        _parse_markdown_issue(path=path, source_dir=source_dir)
        for path in markdown_files
    ]
    imported_payload, synthetic_epic_keys = _build_backlog_payload(
        parsed, source_dir=source_dir
    )
    existing_payload = _load_existing_backlog_payload(output_file=output_file)
    merge_summary = _merge_backlog_payloads(
        imported_payload=imported_payload,
        existing_payload=existing_payload,
        synthetic_epic_keys=synthetic_epic_keys,
    )

    summary = BacklogImportSummary(
        source_dir=source_dir,
        output_file=output_file,
        files_scanned=len(markdown_files),
        epics_imported=merge_summary.epics_imported,
        tickets_imported=merge_summary.tickets_imported,
        synthetic_epics=merge_summary.synthetic_epics,
        epics_skipped_existing=merge_summary.epics_skipped_existing,
        tickets_skipped_existing=merge_summary.tickets_skipped_existing,
    )
    return (
        ScaffoldFile(
            path=output_file, content=json.dumps(merge_summary.payload, indent=2)
        ),
        summary,
    )


def _build_backlog_payload(
    parsed: list[_MarkdownIssue], *, source_dir: Path
) -> tuple[_BacklogPayload, set[str]]:
    epics = [item for item in parsed if item.kind == "epic"]
    tickets = [item for item in parsed if item.kind != "epic"]

    epic_records: dict[str, _EpicPayload] = {}
    epic_order: list[str] = []

    for epic in epics:
        epic_key = epic.key or _derive_key(epic.title or epic.path.stem)
        if epic_key in epic_records:
            raise RuntimeError(f"Duplicate epic key detected during import: {epic_key}")
        epic_records[epic_key] = {
            "key": epic_key,
            "title": epic.title,
            "body": epic.body,
            "labels": _merge_labels(("epic",), epic.labels),
            "assignees": list(epic.assignees),
            "tickets": [],
        }
        epic_order.append(epic_key)

    directory_defaults = _directory_default_epics(epics)
    synthetic_epic_titles: dict[str, str] = {}
    root_key = _derive_key(source_dir.name or "imported")
    real_epic_keys = {
        epic.key or _derive_key(epic.title or epic.path.stem) for epic in epics
    }

    for ticket in tickets:
        epic_key = ticket.epic_key or _resolve_ticket_epic_key(
            ticket=ticket,
            directory_defaults=directory_defaults,
            root_key=root_key,
        )
        if epic_key not in epic_records:
            epic_records[epic_key] = {
                "key": epic_key,
                "title": _synthetic_epic_title(
                    key=epic_key,
                    title_hint=synthetic_epic_titles.get(epic_key)
                    or _directory_title_hint(
                        relative_parent=ticket.relative_parent,
                        source_dir=source_dir,
                    ),
                ),
                "body": _synthetic_epic_body(source_dir=source_dir, epic_key=epic_key),
                "labels": ["epic"],
                "assignees": [],
                "tickets": [],
            }
            epic_order.append(epic_key)

        synthetic_epic_titles.setdefault(
            epic_key,
            _directory_title_hint(
                relative_parent=ticket.relative_parent,
                source_dir=source_dir,
            ),
        )
        ticket_entry: _TicketPayload = {
            "title": ticket.title,
            "body": ticket.body,
            "labels": _merge_labels(("ticket", f"epic:{epic_key}"), ticket.labels),
            "assignees": list(ticket.assignees),
        }
        if ticket.priority:
            ticket_entry["priority"] = ticket.priority
        epic_records[epic_key]["tickets"].append(ticket_entry)

    synthetic_epic_keys = {key for key in epic_order if key not in real_epic_keys}
    payload: _BacklogPayload = {
        "epics": [epic_records[key] for key in epic_order],
    }
    return payload, synthetic_epic_keys


def _load_existing_backlog_payload(*, output_file: Path) -> _BacklogPayload | None:
    if not output_file.exists():
        return None
    try:
        raw = json.loads(output_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Existing backlog JSON is invalid: {output_file}") from exc
    return _normalize_existing_backlog_payload(raw, output_file=output_file)


def _normalize_existing_backlog_payload(
    raw: object, *, output_file: Path
) -> _BacklogPayload:
    if not isinstance(raw, dict):
        raise RuntimeError(f"Existing backlog JSON must be an object: {output_file}")
    epics_raw = raw.get("epics")
    if not isinstance(epics_raw, list):
        raise RuntimeError(
            f"Existing backlog JSON must contain an 'epics' list: {output_file}"
        )

    epics: list[_EpicPayload] = []
    for epic_raw in epics_raw:
        if not isinstance(epic_raw, dict):
            raise RuntimeError(f"Existing epic entry must be an object: {output_file}")
        tickets_raw = epic_raw.get("tickets")
        if not isinstance(tickets_raw, list):
            raise RuntimeError(
                f"Existing epic entry must contain a 'tickets' list: {output_file}"
            )

        tickets: list[_TicketPayload] = []
        for ticket_raw in tickets_raw:
            if not isinstance(ticket_raw, dict):
                raise RuntimeError(
                    f"Existing ticket entry must be an object: {output_file}"
                )
            ticket: _TicketPayload = {
                "title": _require_string_field(
                    ticket_raw, field="title", output_file=output_file
                ),
                "body": _require_string_field(
                    ticket_raw, field="body", output_file=output_file
                ),
                "labels": list(_normalize_string_list(ticket_raw.get("labels"))),
                "assignees": list(_normalize_string_list(ticket_raw.get("assignees"))),
            }
            priority = ticket_raw.get("priority")
            if isinstance(priority, str) and priority.strip():
                ticket["priority"] = priority.strip()
            tickets.append(ticket)

        epics.append(
            {
                "key": _require_string_field(
                    epic_raw, field="key", output_file=output_file
                ),
                "title": _require_string_field(
                    epic_raw, field="title", output_file=output_file
                ),
                "body": _require_string_field(
                    epic_raw, field="body", output_file=output_file
                ),
                "labels": list(_normalize_string_list(epic_raw.get("labels"))),
                "assignees": list(_normalize_string_list(epic_raw.get("assignees"))),
                "tickets": tickets,
            }
        )
    return {"epics": epics}


def _merge_backlog_payloads(
    *,
    imported_payload: _BacklogPayload,
    existing_payload: _BacklogPayload | None,
    synthetic_epic_keys: set[str],
) -> _MergeSummary:
    if existing_payload is None:
        return _MergeSummary(
            payload=imported_payload,
            epics_imported=len(imported_payload["epics"]),
            tickets_imported=sum(
                len(epic["tickets"]) for epic in imported_payload["epics"]
            ),
            synthetic_epics=len(synthetic_epic_keys),
            epics_skipped_existing=0,
            tickets_skipped_existing=0,
        )

    merged_epics = [_copy_epic_payload(epic) for epic in existing_payload["epics"]]
    epic_index = {epic["key"]: epic for epic in merged_epics}
    existing_ticket_titles = {
        ticket["title"] for epic in merged_epics for ticket in epic["tickets"]
    }

    epics_imported = 0
    tickets_imported = 0
    synthetic_epics_imported = 0
    epics_skipped_existing = 0
    tickets_skipped_existing = 0

    for imported_epic in imported_payload["epics"]:
        epic_key = imported_epic["key"]
        new_tickets: list[_TicketPayload] = []
        for ticket in imported_epic["tickets"]:
            if ticket["title"] in existing_ticket_titles:
                tickets_skipped_existing += 1
                continue
            new_tickets.append(_copy_ticket_payload(ticket))
            existing_ticket_titles.add(ticket["title"])

        existing_epic = epic_index.get(epic_key)
        if existing_epic is not None:
            epics_skipped_existing += 1
            existing_epic["tickets"].extend(new_tickets)
            tickets_imported += len(new_tickets)
            continue

        if epic_key in synthetic_epic_keys and not new_tickets:
            continue

        merged_epic = _copy_epic_payload(imported_epic)
        merged_epic["tickets"] = new_tickets
        merged_epics.append(merged_epic)
        epic_index[epic_key] = merged_epic
        epics_imported += 1
        tickets_imported += len(new_tickets)
        if epic_key in synthetic_epic_keys:
            synthetic_epics_imported += 1

    return _MergeSummary(
        payload={"epics": merged_epics},
        epics_imported=epics_imported,
        tickets_imported=tickets_imported,
        synthetic_epics=synthetic_epics_imported,
        epics_skipped_existing=epics_skipped_existing,
        tickets_skipped_existing=tickets_skipped_existing,
    )


def _copy_ticket_payload(ticket: _TicketPayload) -> _TicketPayload:
    copied: _TicketPayload = {
        "title": ticket["title"],
        "body": ticket["body"],
        "labels": list(ticket["labels"]),
        "assignees": list(ticket["assignees"]),
    }
    if "priority" in ticket:
        copied["priority"] = ticket["priority"]
    return copied


def _copy_epic_payload(epic: _EpicPayload) -> _EpicPayload:
    return {
        "key": epic["key"],
        "title": epic["title"],
        "body": epic["body"],
        "labels": list(epic["labels"]),
        "assignees": list(epic["assignees"]),
        "tickets": [_copy_ticket_payload(ticket) for ticket in epic["tickets"]],
    }


def _require_string_field(
    raw: dict[str, object], *, field: str, output_file: Path
) -> str:
    value = raw.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise RuntimeError(
        f"Existing backlog JSON field '{field}' must be a non-empty string: {output_file}"
    )


def _directory_default_epics(epics: list[_MarkdownIssue]) -> dict[Path, str]:
    grouped: dict[Path, list[str]] = {}
    for epic in epics:
        key = epic.key or _derive_key(epic.title or epic.path.stem)
        grouped.setdefault(epic.relative_parent, []).append(key)

    defaults: dict[Path, str] = {}
    for relative_parent, keys in grouped.items():
        if len(keys) == 1:
            defaults[relative_parent] = keys[0]

    if not defaults and len(epics) == 1:
        defaults[Path(".")] = epics[0].key or _derive_key(
            epics[0].title or epics[0].path.stem
        )
    return defaults


def _resolve_ticket_epic_key(
    *,
    ticket: _MarkdownIssue,
    directory_defaults: dict[Path, str],
    root_key: str,
) -> str:
    relative_parent = ticket.relative_parent
    if relative_parent != Path("."):
        current = relative_parent
        while True:
            if current in directory_defaults:
                return directory_defaults[current]
            if current == Path("."):
                break
            current = current.parent
    if Path(".") in directory_defaults:
        return directory_defaults[Path(".")]
    if relative_parent != Path("."):
        return _derive_key(relative_parent.name)
    return root_key


def _directory_title_hint(*, relative_parent: Path, source_dir: Path) -> str:
    if relative_parent == Path("."):
        return _humanize_fragment(source_dir.name)
    return _humanize_fragment(relative_parent.name)


def _synthetic_epic_title(*, key: str, title_hint: str | None) -> str:
    hint = (title_hint or "").strip()
    if hint:
        return f"{hint} - Imported backlog"
    return f"{_humanize_fragment(key)} - Imported backlog"


def _synthetic_epic_body(*, source_dir: Path, epic_key: str) -> str:
    return (
        "## Summary\n"
        f"Synthetic epic created during markdown backlog import from `{source_dir.name}`.\n\n"
        "## Notes\n"
        f"- Epic key: `{epic_key}`\n"
        "- Review and refine this epic after import if you want a more specific milestone description.\n"
    )


def _parse_markdown_issue(*, path: Path, source_dir: Path) -> _MarkdownIssue:
    raw = path.read_text(encoding="utf-8")
    frontmatter, markdown_body = _split_front_matter(raw)
    title, cleaned_body = _extract_title_and_body(
        markdown_body=markdown_body,
        frontmatter=frontmatter,
        fallback_title=_humanize_fragment(path.stem),
    )
    kind = _infer_issue_kind(path=path, frontmatter=frontmatter, title=title)
    labels = _normalize_string_list(frontmatter.get("labels"))
    assignees = _normalize_string_list(frontmatter.get("assignees"))
    priority = _first_string(frontmatter, ("priority",))
    key = _first_string(frontmatter, ("key", "epic_key"))
    epic_key = _first_string(
        frontmatter, ("epic", "epic_key", "milestone")
    ) or _label_epic_key(labels)
    relative_parent = path.relative_to(source_dir).parent

    return _MarkdownIssue(
        path=path,
        relative_parent=relative_parent,
        kind=kind,
        title=title,
        body=cleaned_body,
        labels=labels,
        assignees=assignees,
        priority=priority,
        key=_derive_key(key) if key else None,
        epic_key=_derive_key(epic_key) if epic_key else None,
    )


def _split_front_matter(raw: str) -> tuple[dict[str, object], str]:
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            frontmatter = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1 :])
            return _parse_front_matter(frontmatter), body
    return {}, raw


def _parse_front_matter(raw: str) -> dict[str, object]:
    loaded: dict[str, object] = {}
    list_key: str | None = None
    for raw_line in raw.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if list_key is not None and stripped.startswith("- "):
            current = loaded.setdefault(list_key, [])
            if isinstance(current, list):
                current.append(_strip_matching_quotes(stripped[2:].strip()))
            continue

        list_key = None
        if ":" not in raw_line:
            continue

        key, value = raw_line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if not value:
            loaded[key] = []
            list_key = key
            continue
        loaded[key] = _parse_front_matter_value(value)
    return loaded


def _parse_front_matter_value(raw: str) -> object:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in {'"', "'", "["}:
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            if text.startswith("[") and text.endswith("]"):
                inner = text[1:-1].strip()
                if not inner:
                    return []
                return [
                    _strip_matching_quotes(part)
                    for part in (piece.strip() for piece in inner.split(","))
                    if part
                ]
            return _strip_matching_quotes(text)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
        return parsed
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    return _strip_matching_quotes(text)


def _extract_title_and_body(
    *, markdown_body: str, frontmatter: dict[str, object], fallback_title: str
) -> tuple[str, str]:
    content = _HTML_COMMENT_RE.sub("", markdown_body).strip()
    frontmatter_title = _sanitize_title(_first_string(frontmatter, ("title",)))
    title = frontmatter_title or None
    body = content

    for match in _HEADING_RE.finditer(content):
        heading = _normalize_heading(match.group(2))
        if heading != "title":
            continue
        next_match = _next_heading(content, start=match.end())
        block_end = next_match.start() if next_match is not None else len(content)
        section_body = content[match.end() : block_end]
        extracted = _first_meaningful_line(section_body)
        if extracted:
            title = _sanitize_title(extracted)
        body = f"{content[: match.start()].rstrip()}\n\n{content[block_end:].lstrip()}".strip()
        break

    if title is None:
        first_heading = _HEADING_RE.search(content)
        if first_heading is not None:
            title = _sanitize_title(first_heading.group(2))
            body = f"{content[: first_heading.start()].rstrip()}\n\n{content[first_heading.end() :].lstrip()}".strip()

    resolved_title = title or fallback_title
    resolved_body = body or f"## Summary\nImported from `{fallback_title}`.\n"
    return resolved_title, _normalize_markdown(resolved_body)


def _normalize_markdown(raw: str) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", raw.strip())
    return compact + "\n"


def _next_heading(text: str, *, start: int) -> re.Match[str] | None:
    return _HEADING_RE.search(text, pos=start)


def _first_meaningful_line(raw: str) -> str | None:
    for line in raw.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def _infer_issue_kind(*, path: Path, frontmatter: dict[str, object], title: str) -> str:
    for key in ("type", "name"):
        value = _first_string(frontmatter, (key,))
        if value:
            lowered = value.lower()
            if "epic" in lowered:
                return "epic"
            if "ticket" in lowered:
                return "ticket"

    labels = {
        label.lower() for label in _normalize_string_list(frontmatter.get("labels"))
    }
    if "epic" in labels:
        return "epic"
    if "ticket" in labels:
        return "ticket"

    raw_title = (_first_string(frontmatter, ("title",)) or title).lower()
    if raw_title.startswith("[epic]"):
        return "epic"
    if raw_title.startswith("[ticket]"):
        return "ticket"

    if "epic" in path.stem.lower():
        return "epic"
    return "ticket"


def _merge_labels(defaults: tuple[str, ...], extras: tuple[str, ...]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for label in (*defaults, *extras):
        normalized = label.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _label_epic_key(labels: tuple[str, ...]) -> str | None:
    for label in labels:
        if label.lower().startswith("epic:"):
            return label.split(":", 1)[1].strip()
    return None


def _normalize_string_list(raw: object) -> tuple[str, ...]:
    if isinstance(raw, list):
        return tuple(
            item.strip() for item in (str(value) for value in raw) if item.strip()
        )
    if isinstance(raw, tuple):
        return tuple(
            item.strip() for item in (str(value) for value in raw) if item.strip()
        )
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return ()
        if text.startswith("[") and text.endswith("]"):
            parsed = _parse_front_matter_value(text)
            if parsed == text:
                return tuple(
                    part.strip() for part in text[1:-1].split(",") if part.strip()
                )
            return _normalize_string_list(parsed)
        return tuple(part.strip() for part in text.split(",") if part.strip())
    return ()


def _sanitize_title(raw: str | None) -> str | None:
    if raw is None:
        return None
    title = raw.strip()
    if not title:
        return None
    for prefix in ("[EPIC]", "[Ticket]", "[TICKET]"):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
    return title or None


def _derive_key(raw: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", raw.upper())
    key = "_".join(parts).strip("_")
    return key[:48] or "IMPORTED"


def _normalize_heading(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", raw.lower()).strip()


def _humanize_fragment(raw: str) -> str:
    text = raw.lstrip(".").replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", text).title() or "Imported Backlog"


def _first_string(frontmatter: dict[str, object], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = frontmatter.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _strip_matching_quotes(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
