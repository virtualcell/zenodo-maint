# zenodo-maint

Reusable Zenodo record maintenance for GitHub release archiving — a small,
dependency-free Python CLI plus reusable GitHub Actions and a Claude skill.

It exists because Zenodo's native GitHub webhook is fragile (tied to one
account's OAuth, fails silently). This tool makes archiving reproducible,
loud on failure, and portable across repos.

## Two layers

1. **Repo-independent** — Zenodo record operations (the CLI). Run from anywhere.
2. **Repo-operating** — per-repo automation (the reusable workflows).

No bespoke config: the tool reads the **standard files**.
- **CITATION.cff** — source of truth for citation/authors, and its top-level
  `doi:` (the concept DOI) tells the tool *which record* to write to.
- **.zenodo.json** — the Zenodo deposit metadata (creators, license,
  `related_identifiers`, …). Generate it from CITATION.cff with
  [`cffconvert`](https://github.com/citation-file-format/cffconvert)
  (`cffconvert -f zenodo -o .zenodo.json`) and add the `continues` lineage link.

## Install

```bash
pipx install git+https://github.com/virtualcell/zenodo-maint      # or: pip install -e .
```

Requires Python 3.11+. No third-party dependencies.

## Token

The token must belong to the **account that owns the concept record**. Resolved
from `--token-file`, then `$ZENODO_TOKEN`, then `~/.ssh/zenodo-token`.

## Usage

Mutating commands are **dry-run by default**; add `--execute` to write. Run from
a repo that has a `CITATION.cff` (concept DOI) and `.zenodo.json` (metadata) and
you can omit `--concept`/`--repo` entirely:

```bash
zenodo-maint verify-token
zenodo-maint list-versions                 # concept from CITATION.cff doi:
zenodo-maint check-drift                    # repo from CITATION.cff / $GITHUB_REPOSITORY

# archive one release (metadata from .zenodo.json; date auto-resolved from GitHub)
zenodo-maint archive-release --tag v9.66.0 --execute

# archive a tag but give the record a curated label (e.g. a major-version record):
# the tarball + GitHub link still track --tag; creators/description come from the
# --zenodo-json file; --version/--title override the displayed label.
zenodo-maint --zenodo-json meta/v9.json \
  archive-release --tag v9.66.0 --version 9 --title 'MyProject 9' --date 2024-05-01 --execute

# backfill missed releases from a JSON list of {"tag","date"} (each entry may also
# carry optional "version"/"title" label overrides)
zenodo-maint backfill --tags-file tags.json --execute

# fix a lineage relation across all versions
zenodo-maint relink --from-relation isNewVersionOf --to-relation continues --execute

# re-apply .zenodo.json metadata (e.g. after editing authors) to all versions
zenodo-maint apply-metadata --execute

# edit one existing record — authors/title/description from --zenodo-json, and
# optionally relabel its version/title (leaves every other record untouched)
zenodo-maint --zenodo-json meta/v9.json \
  apply-metadata --record 1234567 --version 9 --title 'MyProject 9' --execute

# scaffold the two standard files for a new repo
zenodo-maint --repo owner/repo bootstrap

# preflight: detect a native-integration conflict, competing concepts, or drift
GH_TOKEN=$(gh auth token) zenodo-maint doctor
```

Outside a configured repo, pass `--concept`, `--repo`, `--citation`, and/or
`--zenodo-json` explicitly. Use `--sandbox` to rehearse against sandbox.zenodo.org.

### Curated / relabeled records

By default a record is labeled by its source tag. To publish a **curated** record
— one whose displayed `version`/`title` differ from the tag, e.g. a single
"major-version" record whose content comes from a chosen build — decouple the
label from the source:

- **Content** (creators/authors, description, license) comes from `--zenodo-json`
  — those fields are too large for flags and often differ per record, so a curated
  record points at its own metadata file.
- **Label** is `--version` / `--title` (or a `version`/`title` in the metadata
  file). Precedence: explicit flag → metadata-file value → the tag.
- **Provenance is preserved:** the uploaded tarball and the `isSupplementTo`
  GitHub link always track the real `--tag`, so the record still cites the exact
  source commit even when it displays a curated label.

Use `archive-release`/`backfill` to create such a record, or `apply-metadata
--record <id>` to relabel and re-author one that already exists.

**Reusing an already-archived tag.** By default (`--dedup-by tag`) a tag that is
already archived is skipped — the conservative behavior for normal release
archiving. A curated record often *reuses* a tag that already exists as a
per-release record (e.g. a `7.7` record built from the already-archived
`7.7.0.15`); pass **`--dedup-by label`** so the tool dedups on the version *label*
instead. Idempotency holds in both modes: a matching label is always skipped, so
re-running a curated mint never creates a duplicate — the mode only controls
whether reusing an already-archived *tag* is allowed.

## Reusable workflows

In a target repo, add a `ZENODO_TOKEN` secret (ideally an **org-level** secret)
and two thin callers:

```yaml
# .github/workflows/zenodo-archive.yml
on: { release: { types: [published] } }
jobs:
  archive:
    uses: virtualcell/zenodo-maint/.github/workflows/archive.reusable.yml@v1
    with: { concept_recid: '21053715', continues_doi: '10.5281/zenodo.5057108', tag: '${{ github.event.release.tag_name }}' }
    secrets: { ZENODO_TOKEN: '${{ secrets.ZENODO_TOKEN }}' }
```
```yaml
# .github/workflows/zenodo-drift.yml
on: { schedule: [{ cron: '0 12 * * 1' }], workflow_dispatch: {} }
jobs:
  drift:
    uses: virtualcell/zenodo-maint/.github/workflows/drift.reusable.yml@v1
    with: { concept_recid: '21053715' }
```

Then **disable the repo's native Zenodo↔GitHub integration** so it can't create a
competing DOI.

## Monitoring many repos

This repo's own `.github/workflows/monitor.yml` checks every repo listed in
[`monitored.json`](monitored.json) on a schedule (a matrix of `{repo, concept}`),
opening a tracking issue here for any that have drifted. Add a repo by appending
to `monitored.json` — no secrets required (public APIs only).

## Avoiding conflicts with the native integration

Only one publisher should archive a repo, or you get duplicate/forked concept
DOIs. Zenodo's native GitHub integration **cannot** be pointed at a pre-existing
record (it always creates its own concept), so it must stay **disabled** when you
use this tool. `doctor` is the preflight gate:

- **webhook check** — flags a `zenodo.org` webhook (needs `GH_TOKEN` with
  repo-admin). This is the reliable signal that the native integration is on.
- **competing-concept check** — tokenless Zenodo search for other concepts
  archiving the repo (best-effort; empty on any search error, so it never
  false-alarms).
- **drift check** — latest GitHub release vs latest Zenodo version.

### Where allowed concepts are recorded
`doctor` treats a concept as expected if it is any of:
1. the target concept (from `CITATION.cff` `doi:`);
2. **referenced in `.zenodo.json` `related_identifiers`** — this is the normal
   home; the pre-fork lineage is already here via `continues`, so it needs no
   separate list;
3. passed via `--allow-concept <id>` (repeatable) — an escape hatch for a
   deliberate second concept you don't want in metadata.

For the central monitor, per-repo allowances can go in `monitored.json`.

## `.zenodo.json` must be JSON

Zenodo validates `.zenodo.json` against its legacy deposit JSON Schema — there is
no YAML variant it reads. Author in **CITATION.cff** (which *is* YAML) and generate
`.zenodo.json` with `cffconvert`. (If both files exist, Zenodo's native integration
uses `.zenodo.json` and ignores CITATION.cff — which is what we want.)

## Development

```bash
uv sync --group dev
uv run ruff check zenodo_maint
uv run mypy            # strict
```

CI (`.github/workflows/ci.yml`) runs ruff + mypy-strict on every push/PR.

**Tracking Zenodo API changes:** Zenodo ships no versioned SDK, so
`.github/workflows/smoke.yml` runs `scripts/sandbox_smoke.py` weekly against
`sandbox.zenodo.org` — exercising create → upload → publish → new-version → edit.
If Zenodo changes the deposit API, that job goes red before production breaks.
Add a `ZENODO_SANDBOX_TOKEN` secret (a token from sandbox.zenodo.org) to enable
it; it skips cleanly without one.

## Claude skill

`skill/SKILL.md` — copy or symlink to `~/.claude/skills/zenodo-maint/` for the
capability (and the judgment/runbook) in every repo's Claude session.

## Versioning

Tag releases (`v1`, `v1.1`, …) and pin consumers by tag/SHA — this tool performs
**irreversible** DOI writes, so never float callers on `main`.
