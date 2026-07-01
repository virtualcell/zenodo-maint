"""zenodo-maint — reusable Zenodo record maintenance for release archiving.

Repo-independent operations against the Zenodo deposit API, driven by the two
standard repo files:

  * .zenodo.json  — deposit metadata (creators, license, related_identifiers, …)
  * CITATION.cff  — the concept DOI (target record) and the repo

Mutating commands default to a dry run; pass --execute to actually write.
Published DOIs are permanent, so --execute is deliberately explicit.
"""
import argparse
import json
import os
import sys
import tempfile
import time

from . import api, config, sources


def _client(args):
    token = config.read_token(args.token_file)
    if not token:
        sys.exit('No Zenodo token. Use --token-file, set $ZENODO_TOKEN, '
                 'or place it at ~/.ssh/zenodo-token')
    return api.ZenodoClient(token, sandbox=args.sandbox)


def _concept(cli, args):
    if args.concept:
        return args.concept
    doi = sources.citation_doi(args.citation)
    if not doi:
        sys.exit('no concept: pass --concept or set a top-level doi: in CITATION.cff')
    return cli.concept_from_doi(doi)


def _repo(args):
    return (args.repo or os.environ.get('GITHUB_REPOSITORY')
            or sources.citation_repo(args.citation)
            or sys.exit('no repo: pass --repo or add a github.com URL to CITATION.cff'))


def _base_metadata(args, inherited):
    """Deposit metadata base: the committed .zenodo.json if present, else the
    metadata inherited from the previous version."""
    zj = sources.read_zenodo_json(args.zenodo_json)
    return dict(zj) if zj else dict(inherited)


# --- commands -----------------------------------------------------------
def cmd_verify_token(args):
    cli = _client(args)
    ok, info = cli.verify_token()
    if not ok:
        sys.exit(f'token check failed: {info}')
    print(f'OK — token authenticates ({"sandbox" if args.sandbox else "production"}).')
    recs = cli.owned_records()
    print(f'account owns {len(recs)} deposition(s):')
    for r in recs[:20]:
        m = r.get('metadata', {})
        print(f"  - {m.get('title', '?')[:46]:46} v{m.get('version')}  concept={r.get('conceptrecid')}")


def cmd_list_versions(args):
    cli = _client(args)
    for x in cli.concept_versions(_concept(cli, args)):
        m = x['metadata']
        print(f"  {m.get('version'):12} {m.get('publication_date'):12} "
              f"deposited={x.get('created','')[:10]}  id={x['id']}  doi={m.get('doi')}")


def cmd_check_drift(args):
    cli = _client(args)
    gh_tag, _ = api.latest_github_release(_repo(args))
    zen = cli.latest_version(_concept(cli, args))['metadata'].get('version')
    print(f'latest GitHub release : {gh_tag}')
    print(f'latest Zenodo version : {zen}')
    if gh_tag == zen:
        print('IN SYNC')
        return
    print('DRIFT — latest release is not archived on Zenodo')
    sys.exit(2)


def _archive_one(cli, args, concept, repo, tag, date, workdir, existing):
    if tag in existing:
        print(f'  {tag}: already archived (id {existing[tag]["id"]}) — skip')
        return
    if not args.execute:
        print(f'  {tag} ({date}): DRY-RUN — would add as new version of concept {concept}')
        return
    latest = cli.latest_version(concept)
    tar = api.github_tarball(repo, tag, workdir)
    draft = cli.new_version(latest['id'])
    cli.replace_files(draft, tar)
    md = _base_metadata(args, draft['metadata'])
    md['version'] = tag
    md['publication_date'] = date
    md['related_identifiers'] = api.with_lineage(
        md.get('related_identifiers'), args.continues, repo, tag)
    md.setdefault('upload_type', 'software')
    cli.set_metadata(draft['id'], md)
    r = cli.publish(draft['id'])
    print(f'  {tag}: PUBLISHED -> {r.get("doi")}')


def cmd_archive_release(args):
    cli = _client(args)
    concept, repo = _concept(cli, args), _repo(args)
    tag, date = args.tag, args.date
    if tag == 'latest':
        tag, latest_date = api.latest_github_release(repo)
        date = date or latest_date
    elif not date:
        date = api.github_release_date(repo, tag)
    if not date:
        sys.exit('could not determine publication date; pass --date YYYY-MM-DD')
    existing = {x['metadata'].get('version'): x for x in cli.concept_versions(concept)}
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} archive {tag} -> concept {concept}')
    with tempfile.TemporaryDirectory() as wd:
        _archive_one(cli, args, concept, repo, tag, date, wd, existing)


def cmd_backfill(args):
    cli = _client(args)
    concept, repo = _concept(cli, args), _repo(args)
    tags = json.load(open(args.tags_file))  # [{"tag","date"}]
    existing = {x['metadata'].get('version'): x for x in cli.concept_versions(concept)}
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} backfill {len(tags)} tag(s) -> concept {concept}')
    with tempfile.TemporaryDirectory() as wd:
        for t in tags:
            _archive_one(cli, args, concept, repo, t['tag'], t['date'], wd, existing)
            if args.execute:
                time.sleep(2)


def cmd_relink(args):
    cli = _client(args)
    concept = _concept(cli, args)
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} relink {args.from_relation} '
          f'-> {args.to_relation} on concept {concept}')
    for x in cli.concept_versions(concept):
        rels = x['metadata'].get('related_identifiers', [])
        if not any(r.get('relation') == args.from_relation for r in rels):
            continue
        ver = x['metadata'].get('version')
        if not args.execute:
            print(f'  {ver}: would relink')
            continue
        d = cli.edit(x['id'])
        for r in d['metadata'].get('related_identifiers', []):
            if r.get('relation') == args.from_relation:
                r['relation'] = args.to_relation
        cli.set_metadata(x['id'], d['metadata'])
        cli.publish(x['id'])
        print(f'  {ver}: relinked')


def cmd_apply_metadata(args):
    """Apply .zenodo.json metadata to records, preserving each version's version +
    publication_date (and per-version GitHub supplement link)."""
    zj = sources.read_zenodo_json(args.zenodo_json)
    if not zj:
        sys.exit(f'no metadata file found at {args.zenodo_json}')
    cli = _client(args)
    concept, repo = _concept(cli, args), _repo(args)
    targets = [cli.get(args.record)] if args.record else cli.concept_versions(concept)
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} apply {args.zenodo_json} '
          f'to {len(targets)} record(s)')
    for x in targets:
        ver = x['metadata'].get('version')
        if not args.execute:
            print(f'  {ver}: would apply metadata')
            continue
        d = cli.edit(x['id'])
        md = dict(zj)
        md['version'] = ver
        md['publication_date'] = x['metadata'].get('publication_date')
        md['related_identifiers'] = api.with_lineage(
            zj.get('related_identifiers'), args.continues, repo, ver)
        md.setdefault('upload_type', 'software')
        cli.set_metadata(x['id'], md)
        cli.publish(x['id'])
        print(f'  {ver}: metadata applied')


# --- parser -------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog='zenodo-maint', description=__doc__)
    p.add_argument('--token-file', help='file containing the Zenodo token')
    p.add_argument('--sandbox', action='store_true', help='use sandbox.zenodo.org')
    p.add_argument('--concept', help='concept record id (default: from CITATION.cff doi)')
    p.add_argument('--repo', help='owner/repo (default: $GITHUB_REPOSITORY or CITATION.cff)')
    p.add_argument('--continues', help='DOI to link via "continues" if not already in metadata')
    p.add_argument('--zenodo-json', default='.zenodo.json', help='deposit metadata file')
    p.add_argument('--citation', default='CITATION.cff', help='citation file (concept DOI + repo)')
    p.add_argument('--execute', action='store_true', help='actually write (default: dry run)')
    sub = p.add_subparsers(dest='cmd', required=True)

    sub.add_parser('verify-token').set_defaults(func=cmd_verify_token)
    sub.add_parser('list-versions').set_defaults(func=cmd_list_versions)
    sub.add_parser('check-drift').set_defaults(func=cmd_check_drift)

    a = sub.add_parser('archive-release', help='archive one release tag as a new version')
    a.add_argument('--tag', required=True, help='release tag, or "latest"')
    a.add_argument('--date', help='publication date YYYY-MM-DD (default: from the release)')
    a.set_defaults(func=cmd_archive_release)

    b = sub.add_parser('backfill', help='archive many tags from a JSON list')
    b.add_argument('--tags-file', required=True, help='JSON list of {"tag","date"}')
    b.set_defaults(func=cmd_backfill)

    r = sub.add_parser('relink', help='change a related_identifiers relation on all versions')
    r.add_argument('--from-relation', default='isNewVersionOf')
    r.add_argument('--to-relation', default='continues')
    r.set_defaults(func=cmd_relink)

    m = sub.add_parser('apply-metadata', help='apply .zenodo.json to all versions (or one record)')
    m.add_argument('--record', help='limit to a single deposition id')
    m.set_defaults(func=cmd_apply_metadata)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except api.ZenodoError as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
