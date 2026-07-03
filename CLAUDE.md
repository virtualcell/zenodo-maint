# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`zenodo-maint` is a **stdlib-only** Python CLI (Python 3.11+, zero third-party
runtime deps — this is a hard design constraint, see `pyproject.toml`
`dependencies = []`) plus reusable GitHub Actions workflows and a Claude skill.
It maintains Zenodo archival records for GitHub release archiving, replacing
Zenodo's fragile native GitHub webhook with a reproducible, loud-on-failure tool.

Read `README.md` for the full user-facing command surface and `skill/SKILL.md`
for the operational decision guidance (when to fork vs. chase credentials,
authorship conventions, the "only one publisher" conflict rule).

## Commands

```bash
uv sync --group dev                      # install dev deps (mypy, ruff)
uv run ruff check zenodo_maint           # lint
uv run mypy                              # type-check (strict; files=["zenodo_maint"])
python -m unittest discover -s tests     # run tests (stdlib unittest, no pytest)
python -m unittest tests.test_metadata_overrides.EffectiveVersion  # single test class
uv run zenodo-maint <cmd>                # run the CLI from a clone
```

CI (`.github/workflows/ci.yml`) runs ruff + mypy-strict on every push/PR. Tests
use stdlib `unittest` only (no pytest) to preserve the dependency-free property.

## Architecture

Four modules under `zenodo_maint/`, split by dependency direction:

- **`sources.py`** — reads the two *standard* repo files. There is **no bespoke
  config**: `CITATION.cff` supplies the concept DOI (its top-level `doi:`, parsed
  by a column-0 regex line-scan — no YAML dep) and the repo; `.zenodo.json`
  supplies deposit metadata (creators, license, `related_identifiers`). This is
  why most commands work with no flags when run inside a configured repo.
- **`config.py`** — token resolution only: `--token-file` → `$ZENODO_TOKEN` →
  `~/.ssh/zenodo-token` / `~/.config/zenodo-token`.
- **`api.py`** — `ZenodoClient` (deposit-API HTTP via `urllib`) plus free
  functions for public/tokenless reads (`public_*`), GitHub reads, and lineage
  helpers. All mutating client helpers are **pure functions of their inputs** —
  they do NOT gate on dry-run. The docstring lists the hard-won Zenodo API
  gotchas (octet-stream Content-Type, inherited-file deletion on new version,
  edit→update→publish, 504-on-success, `q=conceptrecid:` search).
- **`cli.py`** — argparse subcommands (`cmd_*` functions) and the resolution
  helpers (`_concept`, `_repo`, `_effective_version`, `_skip_reason`,
  `_creators_equal`). **The CLI owns the `--execute` gate**: every mutating
  command is dry-run by default and only writes when `--execute` is passed.
  Published DOIs are permanent, so this gate is deliberate — preserve it.

Entry point: `zenodo_maint.cli:main` (see `[project.scripts]`).

## Conventions that matter

- **Dry-run by default.** Any new mutating command must default to dry-run and
  print its plan; actual writes happen only under `--execute`. Keep the write
  logic in pure `api.py` helpers and the gate in `cli.py`.
- **Version label vs. source tag.** A record's displayed `version`/`title` label
  can be decoupled from the source `--tag` (curated/major-version records).
  Precedence lives in `_effective_version` (explicit flag → committed
  `.zenodo.json` value → tag); `_skip_reason` handles idempotent dedup
  (`--dedup-by tag` vs `label`). These have focused unit tests in
  `tests/test_metadata_overrides.py` — update them when touching that logic.
- **Zenodo ships no versioned SDK.** `.github/workflows/smoke.yml` runs
  `scripts/sandbox_smoke.py` weekly against `sandbox.zenodo.org` (create →
  upload → publish → new-version → edit) to catch API drift before production
  breaks. Use `--sandbox` to rehearse against sandbox.zenodo.org.
- **Ruff lint set** (`pyproject.toml`): `E,F,I,UP,B,SIM,S`; `S101` (asserts) and
  `S310` (urllib to known hosts) are intentionally ignored.

## Reusable workflows

`.github/workflows/{archive,drift}.reusable.yml` are consumed by target repos via
thin callers (see README). `monitor.yml` checks every repo in `monitored.json`
(a `{repo, concept}` matrix) on a schedule and opens tracking issues on drift —
tokenless (public APIs only). Add a monitored repo by appending to
`monitored.json`.
