---
name: zenodo-maint
description: >
  Maintain Zenodo archival records for GitHub release archiving — backfill missed
  releases, archive a new release, fix authorship/DOIs/lineage relations, and set
  up automatic archiving + drift monitoring for a repo. Use when working with
  Zenodo DOIs, CITATION.cff badges, or "our releases stopped archiving to Zenodo".
---

# Zenodo maintenance

Drive the deterministic `zenodo-maint` CLI (this repo) for all Zenodo writes.
**Do not hand-roll Zenodo API calls** — the CLI encodes the gotchas and gates
irreversible writes behind `--execute`.

## Install / token
- `pipx install git+https://github.com/<ORG>/zenodo-maint` (or `pip install -e .` from a clone).
- Token: `~/.ssh/zenodo-token`, or `$ZENODO_TOKEN`, or `--token-file`. It must belong
  to the **account that owns the concept record** (creating versions requires ownership).
- Every mutating command is **dry-run by default**; add `--execute` to write.
  Published DOIs are permanent — always dry-run first and show the plan.

## Common tasks
- Check auth/ownership: `zenodo-maint verify-token`
- List a concept's versions/dates: `zenodo-maint --concept <id> list-versions`
- Is Zenodo behind GitHub? `zenodo-maint --concept <id> --repo <owner/repo> check-drift`
- Archive one release: `zenodo-maint --concept <id> --repo <r> --continues <old-doi> archive-release --tag v9.66.0` (add `--execute`)
- Backfill missed releases: build a JSON `[{"tag","date"}]`, then `... backfill --tags-file tags.json` (dry-run, then `--execute`)
- Fix a relation on all versions: `zenodo-maint --concept <id> relink --from-relation isNewVersionOf --to-relation continues --execute`
- Fix authors on all versions: `zenodo-maint --concept <id> set-authors --authors-file authors.json --execute`

A repo's facts live in `zenodo.toml` (concept_recid, continues_doi, repo, authors_file),
so inside a configured repo you can omit the flags.

## Decision guidance (the judgment the CLI can't make)
- **Can't access the original record's account?** Don't chase credentials — **fork**:
  create a new concept under an account you control, backfill into it, and link the
  old lineage with a `continues` relation. The old DOI stays a frozen citable snapshot.
- **Authorship**: reflect real contributors; PIs (often 0 commits) conventionally go
  last. Keep the same author list across all versions of a concept (inherited
  automatically on new versions). Backfilled records can't change their *deposit* date
  (always "today"), but `publication_date` is set to the real release date.
- **Lineage relation**: use `continues` (not `isNewVersionOf`) for a forked lineage,
  and keep it on **every** version — Zenodo's concept page shows the latest version's
  metadata, so the link must ride forward to stay visible.
- **Going forward**: replace the fragile native Zenodo↔GitHub webhook with the
  reusable `archive.reusable.yml` (loud CI failures, org-level `ZENODO_TOKEN` secret)
  plus `drift.reusable.yml` as a no-secret safety net. Disable the native webhook so
  it can't fork a competing concept.

## API gotchas the CLI already handles (don't relearn these)
- Bucket uploads need `Content-Type: application/octet-stream` (else HTTP 415).
- A new version **inherits** the prior version's files — delete them before uploading.
- Editing a **published** record: `actions/edit` → update metadata → `actions/publish`.
- A gateway **504** can be returned even when the write succeeded — verify, don't blindly retry.
- List versions via deposit search `q=conceptrecid:<id>&all_versions=true` (the public
  `/versions` endpoint 400s unauthenticated).
- Test against **sandbox.zenodo.org** with `--sandbox` before touching production.
