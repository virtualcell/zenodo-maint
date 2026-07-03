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
- `pipx install git+https://github.com/virtualcell/zenodo-maint` (or `pip install -e .` from a clone).
- Token: `~/.ssh/zenodo-token`, or `$ZENODO_TOKEN`, or `--token-file`. It must belong
  to the **account that owns the concept record** (creating versions requires ownership).
- Every mutating command is **dry-run by default**; add `--execute` to write.
  Published DOIs are permanent — always dry-run first and show the plan.

## Standard files (no bespoke config)
- **CITATION.cff** — source of truth for authors/citation; its top-level `doi:` is
  the **concept DOI**, which tells the tool which record to write to.
- **.zenodo.json** — the deposit metadata (creators, license, `related_identifiers`
  incl. the `continues` lineage link). Generate from CITATION.cff with
  `cffconvert -f zenodo -o .zenodo.json`, then hand-add `related_identifiers`.

Run inside a repo that has both files and you can omit `--concept`/`--repo`.

## Onboarding a new repo (the full sequence)
`bootstrap` is **only step 1** — it scaffolds the two files locally and makes no
Zenodo calls, mints no concept, and installs no workflows. The rest is manual:

1. **Scaffold** — `zenodo-maint --repo owner/repo bootstrap` writes starter
   `CITATION.cff` + `.zenodo.json` (TODO authors, MIT license, no DOI yet).
2. **Fill authors** in `CITATION.cff`, then sync metadata:
   `cffconvert -f zenodo -o .zenodo.json` and hand-add `related_identifiers`
   (incl. any `continues` lineage link).
3. **Get the concept DOI** — it doesn't exist until the first archive. Either
   `archive-release --tag <first> --execute` (archives into a fresh concept), or
   for a fork mint one explicitly with `create-concept` (see Decision guidance).
4. **Record the concept DOI** — paste the returned concept DOI into
   `CITATION.cff`'s top-level `doi:` so later commands resolve the record.
5. **Install the workflows** — copy the two thin caller YAMLs from the repo
   README into `.github/workflows/` (`archive.reusable.yml@v1` on release +
   `drift.reusable.yml@v1` on a schedule); add the `ZENODO_TOKEN` secret
   (ideally org-level).
6. **Disable the native Zenodo↔GitHub integration** and confirm with
   `GH_TOKEN=$(gh auth token) zenodo-maint doctor` (no webhook, no competing
   concept, no drift).
7. **(optional) Add to central monitoring** — append `{repo, concept}` to this
   repo's `monitored.json`.

## Common tasks
- Check auth/ownership: `zenodo-maint verify-token`
- List every concept/DOI this account owns (+ source repo): `zenodo-maint list-owned` (add `--repo-only` for GitHub-linked only, `--json` for machine output). Authoritative answer to "which repos are actually tracked in Zenodo?" — read from Zenodo, not a hand-kept list.
- List versions/dates: `zenodo-maint list-versions`
- Is the latest GitHub release archived on Zenodo? `zenodo-maint check-drift` (public/tokenless). This is a **membership** test — "is a version labeled the latest release tag published in the concept?" — not equality against Zenodo's newest-by-`created` version, which a backfill of old releases or a curated rollup would otherwise displace into false drift.
- Scaffold the two standard files for a new repo: `zenodo-maint --repo owner/repo bootstrap` (writes starter `CITATION.cff` + `.zenodo.json`).
- Archive one release: `zenodo-maint archive-release --tag v9.66.0` (add `--execute`)
- Backfill missed releases: build a JSON `[{"tag","date"}]`, then `zenodo-maint backfill --tags-file tags.json` (dry-run, then `--execute`)
- Rename a relation on all versions: `zenodo-maint relink --from-relation isNewVersionOf --to-relation continues --execute`
- Re-apply metadata after editing `.zenodo.json` (e.g. authors): `zenodo-maint apply-metadata --execute`
- Fix only the author list on existing records, preserving their title/description (e.g. correct authorship without losing per-release notes): `zenodo-maint --zenodo-json authors.json apply-metadata --creators-only [--version-prefix 7.7.] --execute`. Swaps only `creators`; skips records already matching (idempotent); `--version-prefix` scopes to a major's records.
- Fix only the license across every version (e.g. a mis-declared license), preserving each record's curated title/description/creators: `zenodo-maint --zenodo-json .zenodo.json apply-metadata --license-only --execute`. Swaps only `license` (from the metadata file's `license`); skips records already matching, treating Zenodo's stored `mit-license` as equal to the SPDX `mit` you write in `.zenodo.json` (idempotent). Use for a whole-concept license correction without disturbing curated per-version titles.
- Curated / relabeled record (label decoupled from the source tag): `zenodo-maint --zenodo-json meta/rec.json archive-release --tag <build> --version <label> --title '<Title>' --date <YYYY-MM-DD> --execute`. Creators/description come from the `--zenodo-json` file; `--version`/`--title` set the displayed label; the tarball + GitHub link still track `--tag`. `backfill` entries may carry the same `version`/`title` overrides per tag. To relabel/re-author a record that already exists: `apply-metadata --record <id> --version <label> --title '<Title>'`. If the curated record reuses a tag that is already archived as a per-release record, add `--dedup-by label` (default `tag` skips on the source tag; `label` dedups on the version label so the reuse is allowed — idempotency is preserved either way since a matching label is always skipped).
- Preflight before wiring up a repo: `GH_TOKEN=$(gh auth token) zenodo-maint doctor` — flags a native-integration `zenodo.org` webhook (must be disabled), competing/forked concepts, and drift.
- Audit repo config hygiene (one repo, or many via `--monitored monitored.json`): `GH_TOKEN=$(gh auth token) zenodo-maint audit [--monitored monitored.json]`. Checks the standard files are present, both reusable workflows are pinned at the floating `@vN` (a `@vX.Y.Z`/SHA pin silently freezes a consumer on old code — this is the check that protects the moving-tag strategy), and `.zenodo.json` creators **and title** match the published record (title is what people see on the DOI page, so a mismatch is a hard problem — a curated repo whose record carries a per-version title will flag until its `.zenodo.json` title is reconciled). Tokenless but set `$GH_TOKEN` to beat GitHub's 60-req/hour unauthenticated limit; exits non-zero on problems. Complements `doctor` (webhook/competing-concept/drift). It does NOT diff `.zenodo.json` against `CITATION.cff` — that's `cffconvert`'s job (regenerate and diff by hand).

Outside a configured repo, pass `--concept`, `--repo`, `--citation`, `--zenodo-json`.

## Conflict rule (only one publisher)
The native Zenodo↔GitHub integration cannot target a pre-existing record — it
always forks its own concept — so it must stay **disabled**; this tool is the sole
publisher. `doctor` gates on that. A concept is "expected" if it's the target
(`CITATION.cff` doi), referenced in `.zenodo.json` `related_identifiers` (where the
`continues` lineage already lives — the normal place to record allowances), or
passed via `--allow-concept`.

## Decision guidance (the judgment the CLI can't make)
- **Can't access the original record's account?** Don't chase credentials — **fork**:
  `zenodo-maint --repo owner/repo --continues <old-concept-doi> --zenodo-json meta.json create-concept --tag latest --execute` mints a NEW concept under an account you control (metadata/authors from `--zenodo-json`, lineage via `--continues`), and prints the new concept id for the repo's CITATION.cff. Backfill more versions into it with `archive-release --concept <new-id>` if wanted. The old DOI stays a frozen citable snapshot.
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
- The **public** records API is rate-limited (~30 req/min) and caps page size at **25**
  (authenticated deposit search allows 100). Don't enumerate all versions tokenlessly —
  paging a large concept 429s mid-scan and silently undercounts. To ask "is tag X
  archived?", query the one tag: `q=conceptrecid:<id> AND metadata.version:"<tag>"`
  (exact phrase match — `"8.0"` does not match `8.0.0.06`).
- Test against **sandbox.zenodo.org** with `--sandbox` before touching production.
