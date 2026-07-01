"""Deterministic Zenodo deposit-API client.

Encapsulates the behaviours discovered the hard way:
  * file uploads to the bucket need Content-Type: application/octet-stream (else 415)
  * a new version inherits the previous version's metadata + files; the inherited
    files must be deleted before uploading the release tarball
  * editing a *published* record requires edit -> update -> publish
  * gateway 504s can be returned even when the write actually succeeded
  * listing a concept's versions works via the deposit search q=conceptrecid:<id>

Stdlib only. All mutating helpers are pure functions of their inputs; the CLI is
responsible for the --dry-run / --execute gate.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

PROD = 'https://zenodo.org/api'
SANDBOX = 'https://sandbox.zenodo.org/api'


class ZenodoError(RuntimeError):
    pass


class ZenodoClient:
    def __init__(self, token, sandbox=False):
        if not token:
            raise ZenodoError('no Zenodo token provided')
        self.base = SANDBOX if sandbox else PROD
        self.token = token

    # --- low level -------------------------------------------------------
    def _call(self, method, url, data=None, raw=False, auth=True, parse=True):
        headers = {}
        if auth:
            headers['Authorization'] = f'Bearer {self.token}'
        body = None
        if raw:
            body, headers['Content-Type'] = data, 'application/octet-stream'
        elif data is not None:
            body = json.dumps(data).encode()
            headers['Content-Type'] = 'application/json'
        if not url.startswith('http'):
            url = self.base + url
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req) as r:
                c = r.read()
                return r.status, (json.loads(c) if c and parse else c)
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode(errors='replace')

    def _expect(self, ok, status, payload, what):
        if status not in ok:
            raise ZenodoError(f'{what} failed: HTTP {status}: {payload}')
        return payload

    # --- account / discovery --------------------------------------------
    def verify_token(self):
        st, d = self._call('GET', '/deposit/depositions?size=1')
        return st == 200, (d if st == 200 else str(d))

    def owned_records(self, size=50):
        st, d = self._call('GET', f'/deposit/depositions?size={size}&sort=mostrecent')
        self._expect((200,), st, d, 'list owned depositions')
        return d

    def concept_versions(self, concept_recid):
        """All published depositions in a concept, oldest->newest by created."""
        q = urllib.parse.quote(f'conceptrecid:{concept_recid}')
        st, d = self._call('GET', f'/deposit/depositions?q={q}&all_versions=true&size=100')
        self._expect((200,), st, d, 'list concept versions')
        pub = [x for x in d if x.get('submitted')]
        pub.sort(key=lambda x: x.get('created', ''))
        return pub

    def latest_version(self, concept_recid):
        pub = self.concept_versions(concept_recid)
        if not pub:
            raise ZenodoError(f'no published version in concept {concept_recid}')
        return max(pub, key=lambda x: x.get('created', ''))

    def get(self, dep_id):
        st, d = self._call('GET', f'/deposit/depositions/{dep_id}')
        return self._expect((200,), st, d, f'get deposition {dep_id}')

    # --- mutations -------------------------------------------------------
    def create_deposition(self, metadata=None):
        st, d = self._call('POST', '/deposit/depositions', data={'metadata': metadata or {}})
        return self._expect((200, 201), st, d, 'create deposition')

    def new_version(self, latest_id):
        st, d = self._call('POST', f'/deposit/depositions/{latest_id}/actions/newversion')
        self._expect((200, 201), st, d, 'new version')
        st, draft = self._call('GET', d['links']['latest_draft'])
        return self._expect((200,), st, draft, 'get new-version draft')

    def edit(self, dep_id):
        st, d = self._call('POST', f'/deposit/depositions/{dep_id}/actions/edit')
        # 400 "already editing" is fine — a draft edit already exists.
        if st not in (200, 201, 400):
            raise ZenodoError(f'edit unlock failed: HTTP {st}: {d}')
        return self.get(dep_id)

    def replace_files(self, draft, filepath, filename=None):
        filename = filename or os.path.basename(filepath)
        for f in draft.get('files', []):
            self._call('DELETE', f"/deposit/depositions/{draft['id']}/files/{f['id']}")
        with open(filepath, 'rb') as fh:
            st, d = self._call('PUT', f"{draft['links']['bucket']}/{filename}",
                               data=fh.read(), raw=True, parse=False)
        self._expect((200, 201), st, d, f'upload {filename}')

    def set_metadata(self, dep_id, metadata):
        md = dict(metadata)
        md.pop('doi', None)
        md.pop('prereserve_doi', None)
        st, d = self._call('PUT', f'/deposit/depositions/{dep_id}', data={'metadata': md})
        return self._expect((200,), st, d, f'update metadata {dep_id}')

    def publish(self, dep_id):
        st, d = self._call('POST', f'/deposit/depositions/{dep_id}/actions/publish')
        return self._expect((202,), st, d, f'publish {dep_id}')


# --- helpers ------------------------------------------------------------
def github_tarball(repo, tag, dest_dir):
    """Download a tag's source tarball from GitHub; return the local path."""
    fn = os.path.join(dest_dir, f"{repo.split('/')[-1]}-{tag}.tar.gz")
    if os.path.exists(fn) and os.path.getsize(fn) > 1000:
        return fn
    url = f'https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz'
    urllib.request.urlretrieve(url, fn)
    if os.path.getsize(fn) < 1000:
        raise ZenodoError(f'tarball for {tag} is suspiciously small')
    return fn


def _gh(url):
    req = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def latest_github_release(repo):
    """Latest published (non-prerelease) release tag; public API, no auth."""
    d = _gh(f'https://api.github.com/repos/{repo}/releases/latest')
    return d.get('tag_name'), (d.get('published_at') or '')[:10]


def github_release_date(repo, tag):
    """Publication date (YYYY-MM-DD) of a specific release tag."""
    d = _gh(f'https://api.github.com/repos/{repo}/releases/tags/{tag}')
    return (d.get('published_at') or '')[:10]


def lineage_related(continues_doi, repo, tag):
    """Standard related_identifiers for a release version."""
    rels = []
    if continues_doi:
        rels.append({'relation': 'continues', 'identifier': continues_doi, 'scheme': 'doi'})
    rels.append({'relation': 'isSupplementTo',
                 'identifier': f'https://github.com/{repo}/tree/{tag}', 'scheme': 'url'})
    return rels
