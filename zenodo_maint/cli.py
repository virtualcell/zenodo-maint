"""zenodo-maint — reusable Zenodo record maintenance for release archiving.

Repo-independent operations against the Zenodo deposit API. Mutating commands
default to a dry run; pass --execute to actually write. Published DOIs are
permanent, so --execute is deliberately explicit.
"""
import argparse
import json
import os
import sys
import tempfile
import time

from . import api, config


def _client(args):
    token = config.read_token(args.token_file)
    if not token:
        sys.exit('No Zenodo token. Use --token-file, set $ZENODO_TOKEN, '
                 'or place it at ~/.ssh/zenodo-token')
    return api.ZenodoClient(token, sandbox=args.sandbox)


def _cfg(args):
    return config.load_file(args.config)


def _need(val, name):
    if not val:
        sys.exit(f'missing required setting: {name} (pass --{name.replace("_", "-")} '
                 f'or set it in zenodo.toml)')
    return val


# --- commands -----------------------------------------------------------
def cmd_verify_token(args):
    ok, info = _client(args).verify_token()
    if not ok:
        sys.exit(f'token check failed: {info}')
    recs = _client(args).owned_records()
    print(f'OK — token authenticates ({"sandbox" if args.sandbox else "production"}).')
    print(f'account owns {len(recs)} deposition(s):')
    for r in recs[:20]:
        m = r.get('metadata', {})
        print(f"  - {m.get('title', '?')[:50]:50}  v{m.get('version')}  concept={r.get('conceptrecid')}")


def cmd_list_versions(args):
    cfg = _cfg(args)
    concept = _need(config.resolve(args.concept, None, cfg, 'concept_recid'), 'concept')
    for x in _client(args).concept_versions(concept):
        m = x['metadata']
        print(f"  {m.get('version'):12} {m.get('publication_date'):12} "
              f"deposited={x.get('created','')[:10]}  id={x['id']}  doi={m.get('doi')}")


def cmd_check_drift(args):
    cfg = _cfg(args)
    concept = _need(config.resolve(args.concept, None, cfg, 'concept_recid'), 'concept')
    repo = _need(config.resolve(args.repo, None, cfg, 'repo'), 'repo')
    gh_tag, _ = api.latest_github_release(repo)
    zen = _client(args).latest_version(concept)['metadata'].get('version')
    print(f'latest GitHub release : {gh_tag}')
    print(f'latest Zenodo version : {zen}')
    if gh_tag == zen:
        print('IN SYNC')
        return
    print('DRIFT — latest release is not archived on Zenodo')
    sys.exit(2)


def _archive_one(cli, args, cfg, concept, repo, continues, tag, date, workdir):
    existing = {x['metadata'].get('version'): x for x in cli.concept_versions(concept)}
    if tag in existing:
        print(f'  {tag}: already archived (id {existing[tag]["id"]}) — skip')
        return existing[tag]['id']
    if not args.execute:
        print(f'  {tag} ({date}): DRY-RUN — would add as new version of concept {concept}')
        return None
    latest = cli.latest_version(concept)
    tar = api.github_tarball(repo, tag, workdir)
    draft = cli.new_version(latest['id'])
    cli.replace_files(draft, tar)
    md = dict(draft['metadata'])
    md['version'] = tag
    md['publication_date'] = date
    md['related_identifiers'] = api.lineage_related(continues, repo, tag)
    cli.set_metadata(draft['id'], md)
    r = cli.publish(draft['id'])
    print(f'  {tag}: PUBLISHED -> {r.get("doi")}')
    return r['id']


def cmd_archive_release(args):
    cfg = _cfg(args)
    concept = _need(config.resolve(args.concept, None, cfg, 'concept_recid'), 'concept')
    repo = _need(config.resolve(args.repo, None, cfg, 'repo'), 'repo')
    continues = config.resolve(args.continues, None, cfg, 'continues_doi')
    tag, date = args.tag, args.date
    if tag == 'latest':
        tag, latest_date = api.latest_github_release(repo)
        date = date or latest_date
    elif not date:
        date = api.github_release_date(repo, tag)
    if not date:
        sys.exit('could not determine publication date; pass --date YYYY-MM-DD')
    cli = _client(args)
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} archive {tag} -> concept {concept}')
    with tempfile.TemporaryDirectory() as wd:
        _archive_one(cli, args, cfg, concept, repo, continues, tag, date, wd)


def cmd_backfill(args):
    cfg = _cfg(args)
    concept = _need(config.resolve(args.concept, None, cfg, 'concept_recid'), 'concept')
    repo = _need(config.resolve(args.repo, None, cfg, 'repo'), 'repo')
    continues = config.resolve(args.continues, None, cfg, 'continues_doi')
    if args.tags_file:
        tags = json.load(open(args.tags_file))  # [{"tag": "...", "date": "YYYY-MM-DD"}]
    else:
        sys.exit('--tags-file is required (JSON list of {"tag","date"}); '
                 'auto-discovery from GitHub releases is a TODO')
    cli = _client(args)
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} backfill {len(tags)} tag(s) -> concept {concept}')
    with tempfile.TemporaryDirectory() as wd:
        for t in tags:
            _archive_one(cli, args, cfg, concept, repo, continues, t['tag'], t['date'], wd)
            if args.execute:
                time.sleep(2)


def cmd_relink(args):
    cfg = _cfg(args)
    concept = _need(config.resolve(args.concept, None, cfg, 'concept_recid'), 'concept')
    cli = _client(args)
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
        md = d['metadata']
        for r in md.get('related_identifiers', []):
            if r.get('relation') == args.from_relation:
                r['relation'] = args.to_relation
        cli.set_metadata(x['id'], md)
        cli.publish(x['id'])
        print(f'  {ver}: relinked')


def cmd_set_authors(args):
    cfg = _cfg(args)
    concept = _need(config.resolve(args.concept, None, cfg, 'concept_recid'), 'concept')
    creators = json.load(open(args.authors_file))  # [{"name","orcid","affiliation"}]
    cli = _client(args)
    targets = ([cli.get(args.record)] if args.record
               else cli.concept_versions(concept))
    print(f'{"EXECUTE" if args.execute else "DRY-RUN"} set {len(creators)} author(s) '
          f'on {len(targets)} record(s)')
    for x in targets:
        ver = x['metadata'].get('version')
        if not args.execute:
            print(f'  {ver}: would set authors')
            continue
        d = cli.edit(x['id'])
        md = d['metadata']
        md['creators'] = creators
        cli.set_metadata(x['id'], md)
        cli.publish(x['id'])
        print(f'  {ver}: authors set')


# --- parser -------------------------------------------------------------
def build_parser():
    p = argparse.ArgumentParser(prog='zenodo-maint', description=__doc__)
    p.add_argument('--token-file', help='path to a file containing the Zenodo token')
    p.add_argument('--sandbox', action='store_true', help='use sandbox.zenodo.org')
    p.add_argument('--config', help='path to zenodo.toml (default: ./zenodo.toml if present)')
    p.add_argument('--concept', help='Zenodo concept record id')
    p.add_argument('--repo', help='owner/repo on GitHub')
    p.add_argument('--continues', help='DOI of the pre-fork lineage to link via "continues"')
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

    s = sub.add_parser('set-authors', help='set the author list on all versions (or one record)')
    s.add_argument('--authors-file', required=True, help='JSON list of {"name","orcid","affiliation"}')
    s.add_argument('--record', help='limit to a single deposition id')
    s.set_defaults(func=cmd_set_authors)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except api.ZenodoError as e:
        sys.exit(f'error: {e}')


if __name__ == '__main__':
    main()
