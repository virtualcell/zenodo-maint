"""zenodo-maint — reusable Zenodo record maintenance for release archiving.

Repo-independent operations against the Zenodo deposit API, driven by the two
standard repo files:

  * .zenodo.json  — deposit metadata (creators, license, related_identifiers, …)
  * CITATION.cff  — the concept DOI (target record) and the repo

Mutating commands default to a dry run; pass --execute to actually write.
Published DOIs are permanent, so --execute is deliberately explicit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from typing import Any

from . import api, config, sources


def _client(args: argparse.Namespace) -> api.ZenodoClient:
    token = config.read_token(args.token_file)
    if not token:
        sys.exit("No Zenodo token. Use --token-file, set $ZENODO_TOKEN, "
                 "or place it at ~/.ssh/zenodo-token")
    return api.ZenodoClient(token, sandbox=args.sandbox)


def _concept(cli: api.ZenodoClient, args: argparse.Namespace) -> str:
    if args.concept:
        return str(args.concept)
    doi = sources.citation_doi(args.citation)
    if not doi:
        sys.exit("no concept: pass --concept or set a top-level doi: in CITATION.cff")
    return cli.concept_from_doi(doi)


def _concept_public(args: argparse.Namespace) -> str:
    """Resolve the concept without a Zenodo token (for public-only commands)."""
    if args.concept:
        return str(args.concept)
    doi = sources.citation_doi(args.citation)
    if not doi:
        sys.exit("no concept: pass --concept or set a top-level doi: in CITATION.cff")
    return api.public_concept_from_doi(doi, args.sandbox)


def _repo(args: argparse.Namespace) -> str:
    repo = (args.repo or os.environ.get("GITHUB_REPOSITORY")
            or sources.citation_repo(args.citation))
    if not repo:
        sys.exit("no repo: pass --repo or add a github.com URL to CITATION.cff")
    return str(repo)


def _effective_version(
    override: str | None, base: dict[str, Any], from_file: bool, tag: str
) -> str:
    """Resolve the record's `version` label. Precedence: an explicit --version
    override, then a `version` supplied by a committed .zenodo.json, then the tag.
    A `version` inherited from the previous release (from_file False) is ignored so
    the default stays "label the record by its tag"."""
    if override:
        return override
    if from_file and base.get("version"):
        return str(base["version"])
    return tag


def _skip_reason(
    tag: str, label: str, existing: dict[str, Any], dedup_by: str
) -> str | None:
    """Why (if at all) to skip archiving this tag, given the concept's existing
    versions (keyed by version label). Returns 'label', 'tag', or None.

    A matching version LABEL is always a skip — that guarantees idempotency in both
    modes (re-running a mint never makes a duplicate). In the default 'tag' mode we
    additionally skip if the source TAG is already archived under some other label
    (conservative: don't reuse an archived tag). 'label' mode drops that extra check,
    so a curated record can reuse an already-archived tag."""
    if label in existing:
        return "label"
    if dedup_by == "tag" and tag in existing:
        return "tag"
    return None


def _creators_equal(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    """Order-sensitive comparison of two creator lists on the fields we set
    (name, affiliation, orcid). Used to skip records already carrying the target
    authors so --creators-only is idempotent and cheap to re-run."""
    def key(cs: list[dict[str, Any]]) -> list[tuple[Any, Any, Any]]:
        return [(c.get("name"), c.get("affiliation"), c.get("orcid")) for c in cs]
    return key(a) == key(b)


# --- commands -----------------------------------------------------------
def cmd_verify_token(args: argparse.Namespace) -> None:
    cli = _client(args)
    ok, info = cli.verify_token()
    if not ok:
        sys.exit(f"token check failed: {info}")
    print(f'OK — token authenticates ({"sandbox" if args.sandbox else "production"}).')
    recs = cli.owned_records()
    print(f"account owns {len(recs)} deposition(s):")
    for r in recs[:20]:
        m = r.get("metadata", {})
        title = m.get("title", "?")[:44]
        print(f"  - {title:44} v{m.get('version')}  concept={r.get('conceptrecid')}")


def cmd_list_owned(args: argparse.Namespace) -> None:
    """Every Zenodo concept this account owns, deduped to the latest version, with
    the source GitHub repo — the authoritative 'which repos are tracked' list."""
    cli = _client(args)
    deps = cli.owned_records_all()
    latest: dict[str, dict[str, Any]] = {}   # concept recid -> latest deposition
    for d in deps:
        cid = str(d.get("conceptrecid") or d.get("id"))
        cur = latest.get(cid)
        if cur is None or d.get("created", "") > cur.get("created", ""):
            latest[cid] = d
    rows = []
    for cid, d in latest.items():
        m = d.get("metadata", {})
        rows.append({
            "repo": api.repo_from_related(m) or "",
            "concept_doi": d.get("conceptdoi") or "",
            "concept_recid": cid,
            "latest_doi": m.get("doi") or d.get("doi") or "",
            "version": m.get("version") or "",
            "date": m.get("publication_date") or "",
            "title": m.get("title") or "",
        })
    if args.repo_only:
        rows = [r for r in rows if r["repo"]]
    rows.sort(key=lambda r: (r["repo"] == "", r["repo"].lower(), r["title"].lower()))

    if args.json:
        print(json.dumps(rows, indent=2))
        return
    linked = sum(1 for r in rows if r["repo"])
    print(f"account owns {len(latest)} concept(s); {linked} linked to a GitHub repo:\n")
    w = max([len(r["repo"]) for r in rows] + [4])
    print(f"{'repo':{w}}  {'concept DOI':22}  {'latest':12}  {'date':10}  title")
    print("-" * (w + 62))
    for r in rows:
        print(f"{r['repo'] or '—':{w}}  {(r['concept_doi'] or r['concept_recid']):22}  "
              f"{r['version'][:12]:12}  {r['date']:10}  {r['title'][:50]}")


def cmd_list_versions(args: argparse.Namespace) -> None:
    cli = _client(args)
    for x in cli.concept_versions(_concept(cli, args)):
        m = x["metadata"]
        print(f"  {m.get('version'):12} {m.get('publication_date'):12} "
              f"deposited={x.get('created','')[:10]}  id={x['id']}  doi={m.get('doi')}")


def cmd_check_drift(args: argparse.Namespace) -> None:
    # Public APIs only — no token, so this is safe to run in a secret-less monitor.
    repo = _repo(args)
    concept = _concept_public(args)
    gh_tag, _ = api.latest_github_release(repo)
    zen = api.public_latest_version(concept, args.sandbox)
    print(f"latest GitHub release : {gh_tag}")
    print(f"latest Zenodo version : {zen}")
    if gh_tag == zen:
        print("IN SYNC")
        return
    print("DRIFT — latest release is not archived on Zenodo")
    sys.exit(2)


def _archive_one(
    cli: api.ZenodoClient, args: argparse.Namespace, concept: str, repo: str,
    tag: str, date: str, workdir: str, existing: dict[str, Any],
    version: str | None = None, title: str | None = None,
) -> None:
    # `version`/`title` override the record's displayed label; the tarball and the
    # GitHub supplement link always track the real `tag`. Creators/description come
    # from --zenodo-json (too large for flags), so a curated record supplies those
    # via a per-record metadata file.
    #
    # Dedup strategy is chosen by --dedup-by (see _skip_reason): the default skips on
    # the source tag; 'label' skips only on the resolved version label, so a curated
    # record (e.g. version "7.7") may reuse a tag ("7.7.0.15") already archived as a
    # per-release record. The label always identifies the record we create.
    zj = sources.read_zenodo_json(args.zenodo_json)
    from_file = zj is not None
    label = _effective_version(version, zj or {}, from_file, tag)
    reason = _skip_reason(tag, label, existing, args.dedup_by)
    if reason == "label":
        print(f'  {tag}: version {label!r} already archived '
              f'(id {existing[label]["id"]}) — skip')
        return
    if reason == "tag":
        print(f'  {tag}: tag already archived (id {existing[tag]["id"]}); pass '
              f'--dedup-by label to add curated version {label!r} — skip')
        return
    if not args.execute:
        note = f" as version {label!r}" if label != tag else ""
        if title:
            note += f"{',' if note else ' as'} title {title!r}"
        print(f"  {tag} ({date}): DRY-RUN — would add to concept {concept}{note}")
        return
    latest = cli.latest_version(concept)
    tar = api.github_tarball(repo, tag, workdir)
    draft = cli.new_version(latest["id"])
    cli.replace_files(draft, tar)
    md = dict(zj) if zj is not None else dict(draft["metadata"])
    md["version"] = label
    if title:
        md["title"] = title
    md["publication_date"] = date
    md["related_identifiers"] = api.with_lineage(
        md.get("related_identifiers"), args.continues, repo, tag)
    md.setdefault("upload_type", "software")
    cli.set_metadata(draft["id"], md)
    r = cli.publish(draft["id"])
    print(f'  {tag}: PUBLISHED as {md["version"]} -> {r.get("doi")}')


def cmd_archive_release(args: argparse.Namespace) -> None:
    cli = _client(args)
    concept, repo = _concept(cli, args), _repo(args)
    tag, date = args.tag, args.date
    if tag == "latest":
        tag, latest_date = api.latest_github_release(repo)
        date = date or latest_date
    elif not date:
        date = api.github_release_date(repo, tag)
    if not date:
        sys.exit("could not determine publication date; pass --date YYYY-MM-DD")
    existing = {x["metadata"].get("version"): x for x in cli.concept_versions(concept)}
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} archive {tag} -> concept {concept}')
    with tempfile.TemporaryDirectory() as wd:
        _archive_one(cli, args, concept, repo, tag, date, wd, existing,
                     version=args.version, title=args.title)


def cmd_create_concept(args: argparse.Namespace) -> None:
    """Create a brand-new Zenodo concept from a release tag (the first version).

    Unlike archive-release/backfill (which add a version to an existing concept),
    this mints a fresh concept — the fork case: create a new concept under an
    account you control and link the old lineage with --continues. Metadata
    (creators, title, description, license) comes from --zenodo-json; the new
    concept id is printed for CITATION.cff."""
    zj = sources.read_zenodo_json(args.zenodo_json)
    if not zj:
        sys.exit("create-concept needs --zenodo-json with the deposit metadata")
    cli = _client(args)
    repo = _repo(args)
    tag, date = args.tag, args.date
    if tag == "latest":
        tag, latest_date = api.latest_github_release(repo)
        date = date or latest_date
    elif not date:
        date = api.github_release_date(repo, tag)
    if not date:
        sys.exit("could not determine publication date; pass --date YYYY-MM-DD")
    label = args.version or str(zj.get("version") or tag)
    cont = f", continues {args.continues}" if args.continues else ""
    if not args.execute:
        print(f"DRY-RUN — would CREATE a new concept from {repo}@{tag} ({date}) "
              f"as version {label!r}{cont}")
        return
    with tempfile.TemporaryDirectory() as wd:
        tar = api.github_tarball(repo, tag, wd)
        md = dict(zj)
        md["version"] = label
        if args.title:
            md["title"] = args.title
        md["publication_date"] = date
        md["related_identifiers"] = api.with_lineage(
            md.get("related_identifiers"), args.continues, repo, tag)
        md.setdefault("upload_type", "software")
        dep = cli.create_deposition()          # empty draft → fresh concept
        cli.replace_files(dep, tar)            # upload the release tarball
        cli.set_metadata(dep["id"], md)        # full metadata (validated here)
        r = cli.publish(dep["id"])
        cid = r.get("conceptrecid")
        print(f"CREATED concept {cid}: version {label} -> {r['metadata'].get('doi')}")
        print(f"  concept DOI: 10.5281/zenodo.{cid}  "
              f"(put this in the new repo's CITATION.cff doi:)")


def cmd_backfill(args: argparse.Namespace) -> None:
    cli = _client(args)
    concept, repo = _concept(cli, args), _repo(args)
    with open(args.tags_file) as fh:
        tags = json.load(fh)  # [{"tag","date"[,"version","title"]}]
    existing = {x["metadata"].get("version"): x for x in cli.concept_versions(concept)}
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    print(f"{mode} backfill {len(tags)} tag(s) -> concept {concept}")
    with tempfile.TemporaryDirectory() as wd:
        for t in tags:
            _archive_one(cli, args, concept, repo, t["tag"], t["date"], wd, existing,
                         version=t.get("version"), title=t.get("title"))
            if args.execute:
                time.sleep(2)


def cmd_relink(args: argparse.Namespace) -> None:
    cli = _client(args)
    concept = _concept(cli, args)
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} relink {args.from_relation} '
          f"-> {args.to_relation} on concept {concept}")
    for x in cli.concept_versions(concept):
        rels = x["metadata"].get("related_identifiers", [])
        if not any(r.get("relation") == args.from_relation for r in rels):
            continue
        ver = x["metadata"].get("version")
        if not args.execute:
            print(f"  {ver}: would relink")
            continue
        d = cli.edit(x["id"])
        for r in d["metadata"].get("related_identifiers", []):
            if r.get("relation") == args.from_relation:
                r["relation"] = args.to_relation
        cli.set_metadata(x["id"], d["metadata"])
        cli.publish(x["id"])
        print(f"  {ver}: relinked")


def cmd_apply_metadata(args: argparse.Namespace) -> None:
    """Apply .zenodo.json metadata to records, preserving each version's version +
    publication_date (and per-version GitHub supplement link)."""
    zj = sources.read_zenodo_json(args.zenodo_json)
    if not zj:
        sys.exit(f"no metadata file found at {args.zenodo_json}")
    cli = _client(args)
    concept, repo = _concept(cli, args), _repo(args)
    targets = [cli.get(args.record)] if args.record else cli.concept_versions(concept)
    if args.version_prefix:
        targets = [x for x in targets
                   if str(x["metadata"].get("version", "")).startswith(args.version_prefix)]
    mode = "EXECUTE" if args.execute else "DRY-RUN"

    # --creators-only: swap just the creators list on each record, preserving its
    # title/description/version/date/related_identifiers. Skip records already
    # carrying the target authors (idempotent). Use with --version-prefix to fix a
    # whole major's per-release records without touching their release notes.
    if args.creators_only:
        want = zj.get("creators", [])
        if not want:
            sys.exit("--creators-only needs a non-empty 'creators' list in the metadata file")
        print(f"{mode} set creators only ({len(want)} authors) on {len(targets)} record(s)")
        changed = same = 0
        failed: list[tuple[str, str]] = []
        for x in targets:
            ver = x["metadata"].get("version")
            cur = x["metadata"].get("creators", [])
            if _creators_equal(cur, want):
                same += 1
                continue
            changed += 1
            if not args.execute:
                print(f"  {ver}: would set creators ({len(cur)} -> {len(want)})")
                continue
            # Continue past a record Zenodo rejects (e.g. a pre-existing invalid
            # field it re-validates on submit) so one bad record can't block a batch.
            try:
                md = dict(cli.edit(x["id"])["metadata"])  # current metadata; preserve all
                md["creators"] = want
                cli.set_metadata(x["id"], md)
                cli.publish(x["id"])
                print(f"  {ver}: creators updated ({len(cur)} -> {len(want)})")
            except api.ZenodoError as e:
                changed -= 1
                failed.append((str(ver), str(e)))
                print(f"  {ver}: FAILED (id {x['id']}) — {e}")
        print(f"  ({changed} {'updated' if args.execute else 'to update'}, "
              f"{same} already correct, {len(failed)} failed)")
        if failed:
            sys.exit(f"{len(failed)} record(s) could not be updated: "
                     + ", ".join(v for v, _ in failed))
        return

    if (args.version or args.title) and not args.record:
        print("  ! --version/--title relabel every targeted record; "
              "pair with --record <id> to relabel just one")
    print(f'{mode} apply {args.zenodo_json} to {len(targets)} record(s)')
    for x in targets:
        ver = x["metadata"].get("version")
        # Preserve each record's own version/date unless explicitly overridden, so
        # a bulk author fix never collapses distinct versions onto one label.
        new_ver = args.version or zj.get("version") or ver
        if not args.execute:
            relabel = f" -> version '{new_ver}'" if new_ver != ver else ""
            print(f"  {ver}: would apply metadata{relabel}")
            continue
        cli.edit(x["id"])
        md = dict(zj)
        md["version"] = new_ver
        if args.title:
            md["title"] = args.title
        md["publication_date"] = x["metadata"].get("publication_date")
        md["related_identifiers"] = api.with_lineage(
            zj.get("related_identifiers"), args.continues, repo, ver)
        md.setdefault("upload_type", "software")
        cli.set_metadata(x["id"], md)
        cli.publish(x["id"])
        print(f"  {ver}: metadata applied{'' if new_ver == ver else f' (now {new_ver})'}")


CITATION_TEMPLATE = """\
cff-version: 1.2.0
message: "If you use this software, please cite it using these metadata."
title: {title}
authors:
  - family-names: TODO
    given-names: TODO
    # orcid: 'https://orcid.org/0000-0000-0000-0000'
repository-code: 'https://github.com/{repo}'
license: MIT
# doi: '10.5281/zenodo.XXXXXXX'   # concept DOI — fill in after the first Zenodo archive
"""

ZENODO_JSON_TEMPLATE: dict[str, Any] = {
    "upload_type": "software",
    "title": None,  # filled per repo
    "license": "MIT",
    "creators": [{"name": "TODO, TODO", "affiliation": ""}],
    "related_identifiers": [],
}


def cmd_bootstrap(args: argparse.Namespace) -> None:
    """Scaffold CITATION.cff and .zenodo.json for a repo."""
    repo = (args.repo or os.environ.get("GITHUB_REPOSITORY") or "OWNER/REPO")
    title = args.title or repo.split("/")[-1]
    os.makedirs(args.dir, exist_ok=True)
    cff_path = os.path.join(args.dir, "CITATION.cff")
    zj_path = os.path.join(args.dir, ".zenodo.json")
    for path in (cff_path, zj_path):
        if os.path.exists(path) and not args.force:
            sys.exit(f"{path} already exists (use --force to overwrite)")
    zj = dict(ZENODO_JSON_TEMPLATE)
    zj["title"] = title
    with open(cff_path, "w") as fh:
        fh.write(CITATION_TEMPLATE.format(title=title, repo=repo))
    with open(zj_path, "w") as fh:
        json.dump(zj, fh, indent=2)
        fh.write("\n")
    print(f"wrote {cff_path}\nwrote {zj_path}")
    print("next: fill authors, run `cffconvert -f zenodo -o .zenodo.json` to sync, "
          "add the concept doi: to CITATION.cff after the first archive")


def cmd_doctor(args: argparse.Namespace) -> None:
    """Preflight: detect native-integration conflicts, competing concepts, drift.
    Exits non-zero if any problem is found (usable as a setup gate)."""
    repo = _repo(args)
    concept = _concept_public(args)
    problems = 0
    print(f"doctor: repo={repo} concept={concept}\n")

    # A — native integration webhook (needs a repo-admin GitHub token)
    print("[integration webhook]")
    ghtok = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not ghtok:
        print("  ? skipped — set GH_TOKEN (repo-admin) to check for a zenodo.org webhook")
    else:
        try:
            hooks = api.github_webhooks(repo, ghtok)
            zen = [h for h in hooks if "zenodo.org" in str(h.get("config", {}).get("url", ""))]
            if zen:
                print("  ✗ CONFLICT — a zenodo.org webhook is enabled; the native integration")
                print("    will fork a competing concept on the next release. Disable it.")
                problems += 1
            else:
                print("  ✓ no zenodo.org webhook")
        except Exception as e:  # noqa: BLE001
            print(f"  ? could not read webhooks ({e})")

    # B — competing Zenodo concepts (tokenless, best-effort)
    print("[zenodo concepts]")
    allowed = {str(a) for a in (args.allow_concept or [])}
    st, rec = api.public_get(f"/records/{concept}", args.sandbox)
    if st == 200 and isinstance(rec, dict):
        allowed |= api.concept_ids_in_related(rec.get("metadata", {}))
    zj = sources.read_zenodo_json(args.zenodo_json)
    if zj:
        allowed |= api.concept_ids_in_related(zj)
    found = api.concepts_referencing_repo(repo, args.sandbox)
    conflicts = found - {str(concept)} - allowed
    if conflicts:
        print(f"  ✗ CONFLICT — unexpected concept(s): {', '.join(sorted(conflicts))}")
        print("    if intentional, allow with --allow-concept <id>")
        problems += 1
    else:
        print(f"  ✓ target {concept}; allowed {sorted(allowed) or '[]'}; found {sorted(found)}")

    # C — drift
    print("[drift]")
    gh_tag, _ = api.latest_github_release(repo)
    zen_ver = api.public_latest_version(concept, args.sandbox)
    if gh_tag == zen_ver:
        print(f"  ✓ in sync ({gh_tag})")
    else:
        print(f"  ✗ DRIFT — github={gh_tag} zenodo={zen_ver}")
        problems += 1

    print()
    if problems:
        sys.exit(f"doctor: {problems} problem(s) found")
    print("doctor: healthy")


# --- parser -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zenodo-maint", description=__doc__)
    p.add_argument("--token-file", help="file containing the Zenodo token")
    p.add_argument("--sandbox", action="store_true", help="use sandbox.zenodo.org")
    p.add_argument("--concept", help="concept record id (default: from CITATION.cff doi)")
    p.add_argument("--repo", help="owner/repo (default: $GITHUB_REPOSITORY or CITATION.cff)")
    p.add_argument("--continues", help='DOI to link via "continues" if not already in metadata')
    p.add_argument("--allow-concept", action="append",
                   help="concept id to treat as expected in doctor (repeatable)")
    p.add_argument("--dedup-by", choices=["tag", "label"], default="tag",
                   help="skip a record if its source tag (default) or only its "
                        "version label is already archived; use 'label' for curated "
                        "records that deliberately reuse an already-archived tag")
    p.add_argument("--zenodo-json", default=".zenodo.json", help="deposit metadata file")
    p.add_argument("--citation", default="CITATION.cff", help="citation file (concept DOI + repo)")
    p.add_argument("--execute", action="store_true", help="actually write (default: dry run)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("verify-token").set_defaults(func=cmd_verify_token)
    lo = sub.add_parser("list-owned",
                        help="list every concept/DOI this account owns, with source repo")
    lo.add_argument("--json", action="store_true", help="output JSON")
    lo.add_argument("--repo-only", action="store_true",
                    help="only records linked to a GitHub repo")
    lo.set_defaults(func=cmd_list_owned)
    sub.add_parser("list-versions").set_defaults(func=cmd_list_versions)
    sub.add_parser("check-drift").set_defaults(func=cmd_check_drift)
    sub.add_parser("doctor", help="check for integration conflicts, forks, and drift"
                   ).set_defaults(func=cmd_doctor)

    a = sub.add_parser("archive-release", help="archive one release tag as a new version")
    a.add_argument("--tag", required=True, help='release tag, or "latest"')
    a.add_argument("--date", help="publication date YYYY-MM-DD (default: from the release)")
    a.add_argument("--version", help="version label for the record (default: the tag)")
    a.add_argument("--title", help="title for the record (default: from --zenodo-json)")
    a.set_defaults(func=cmd_archive_release)

    cc = sub.add_parser("create-concept",
                        help="mint a NEW concept from a tag (fork case; use --continues)")
    cc.add_argument("--tag", required=True, help='release tag, or "latest"')
    cc.add_argument("--date", help="publication date YYYY-MM-DD (default: from the release)")
    cc.add_argument("--version", help="version label (default: from --zenodo-json, else tag)")
    cc.add_argument("--title", help="title for the record (default: from --zenodo-json)")
    cc.set_defaults(func=cmd_create_concept)

    b = sub.add_parser("backfill", help="archive many tags from a JSON list")
    b.add_argument("--tags-file", required=True,
                   help='JSON list of {"tag","date"}; each entry may also carry '
                        'optional "version"/"title" label overrides')
    b.set_defaults(func=cmd_backfill)

    r = sub.add_parser("relink", help="change a related_identifiers relation on all versions")
    r.add_argument("--from-relation", default="isNewVersionOf")
    r.add_argument("--to-relation", default="continues")
    r.set_defaults(func=cmd_relink)

    m = sub.add_parser("apply-metadata", help="apply .zenodo.json to all versions (or one record)")
    m.add_argument("--record", help="limit to a single deposition id")
    m.add_argument("--version", help="relabel the record's version (use with --record)")
    m.add_argument("--title", help="relabel the record's title (use with --record)")
    m.add_argument("--creators-only", action="store_true",
                   help="replace only the creators list (keep title/description/"
                        "version/date); skips records whose creators already match")
    m.add_argument("--version-prefix",
                   help="limit to records whose version label starts with this prefix")
    m.set_defaults(func=cmd_apply_metadata)

    bs = sub.add_parser("bootstrap", help="scaffold CITATION.cff and .zenodo.json")
    bs.add_argument("--dir", default=".", help="target directory (default: cwd)")
    bs.add_argument("--title", help="project title (default: repo name)")
    bs.add_argument("--force", action="store_true", help="overwrite existing files")
    bs.set_defaults(func=cmd_bootstrap)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except api.ZenodoError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
