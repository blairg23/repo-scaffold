Store private backlog files here. This directory is git-ignored.

## Recommended layout (per-repo slug)

```
local/backlog/
  <owner>/<repo>/issues.json   ← preferred
```

Example:

```
local/backlog/
  acme/payments-api/issues.json
  acme/other-repo/issues.json
```

`apply backlog` resolves `local/backlog/<owner>/<repo>/issues.json` automatically when
`--repo` is provided and `--file` is omitted.

`import backlog --repo <owner>/<repo>` writes to this path by default.

## Legacy flat layout

`local/backlog/issues.json` still works as a fallback when no slug path exists,
but is ambiguous when managing multiple repos from the same workspace.
