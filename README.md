# zenodo-maint

Reusable Zenodo record maintenance for GitHub release archiving — a small,
dependency-free Python CLI plus reusable GitHub Actions and a Claude skill.

It exists because Zenodo's native GitHub webhook is fragile (tied to one
account's OAuth, fails silently). This tool makes archiving reproducible,
loud on failure, and portable across repos.

## Two layers

1. **Repo-independent** — Zenodo record operations (the CLI). Run from anywhere.
2. **Repo-operating** — per-repo automation (the reusable workflows) + a
   `zenodo.toml` config that keeps each repo's facts in the repo.

## Install

```bash
pipx install git+https://github.com/<ORG>/zenodo-maint      # or: pip install -e .
```

Requires Python 3.11+. No third-party dependencies.

## Token

The token must belong to the **account that owns the concept record**. Resolved
from `--token-file`, then `$ZENODO_TOKEN`, then `~/.ssh/zenodo-token`.

## Usage

Mutating commands are **dry-run by default**; add `--execute` to write.

```bash
zenodo-maint verify-token
zenodo-maint --concept 21053715 list-versions
zenodo-maint --concept 21053715 --repo owner/repo check-drift

# archive one release (date auto-resolved from GitHub)
zenodo-maint --concept 21053715 --repo owner/repo \
  --continues 10.5281/zenodo.5057108 archive-release --tag v9.66.0 --execute

# backfill missed releases from a JSON list of {"tag","date"}
zenodo-maint --concept 21053715 --repo owner/repo backfill --tags-file tags.json --execute

# fix a lineage relation across all versions
zenodo-maint --concept 21053715 relink --from-relation isNewVersionOf --to-relation continues --execute

# set the author list across all versions
zenodo-maint --concept 21053715 set-authors --authors-file authors.json --execute
```

Put a `zenodo.toml` (see `examples/`) in a repo to omit the flags there.
Use `--sandbox` to exercise everything against sandbox.zenodo.org first.

## Reusable workflows

In a target repo, add a `ZENODO_TOKEN` secret (ideally an **org-level** secret)
and two thin callers:

```yaml
# .github/workflows/zenodo-archive.yml
on: { release: { types: [published] } }
jobs:
  archive:
    uses: <ORG>/zenodo-maint/.github/workflows/archive.reusable.yml@v1
    with: { concept_recid: '21053715', continues_doi: '10.5281/zenodo.5057108', tag: '${{ github.event.release.tag_name }}' }
    secrets: { ZENODO_TOKEN: '${{ secrets.ZENODO_TOKEN }}' }
```
```yaml
# .github/workflows/zenodo-drift.yml
on: { schedule: [{ cron: '0 12 * * 1' }], workflow_dispatch: {} }
jobs:
  drift:
    uses: <ORG>/zenodo-maint/.github/workflows/drift.reusable.yml@v1
    with: { concept_recid: '21053715' }
```

Then **disable the repo's native Zenodo↔GitHub integration** so it can't create a
competing DOI.

## Claude skill

`skill/SKILL.md` — copy or symlink to `~/.claude/skills/zenodo-maint/` for the
capability (and the judgment/runbook) in every repo's Claude session.

## Versioning

Tag releases (`v1`, `v1.1`, …) and pin consumers by tag/SHA — this tool performs
**irreversible** DOI writes, so never float callers on `main`.
