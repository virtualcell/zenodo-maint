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
from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from typing import Any

PROD = "https://zenodo.org/api"
SANDBOX = "https://sandbox.zenodo.org/api"

# Socket timeout (seconds) for every request. Zenodo occasionally leaves a
# connection open after a write without sending the response, which would hang an
# unbounded run forever; a timeout turns that into a retriable/resumable error.
# It's a per-blocking-operation timeout, so active large uploads (data still
# flowing) are unaffected — only genuine stalls trip it.
REQUEST_TIMEOUT = 120

Json = Any
Metadata = dict[str, Any]


class ZenodoError(RuntimeError):
    pass


class ZenodoClient:
    def __init__(self, token: str, sandbox: bool = False) -> None:
        if not token:
            raise ZenodoError("no Zenodo token provided")
        self.base = SANDBOX if sandbox else PROD
        self.token = token

    # --- low level -------------------------------------------------------
    def _call(
        self,
        method: str,
        url: str,
        data: Json | bytes | None = None,
        raw: bool = False,
        auth: bool = True,
        parse: bool = True,
    ) -> tuple[int, Json]:
        headers: dict[str, str] = {}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        body: bytes | None = None
        if raw:
            assert isinstance(data, (bytes, bytearray))
            body, headers["Content-Type"] = bytes(data), "application/octet-stream"
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if not url.startswith("http"):
            url = self.base + url
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        # Retry only idempotent GETs on transient network faults (truncated reads,
        # connection resets). Never auto-retry a write: a 504 can mask a successful
        # write, so a blind retry could double-create.
        attempts = 4 if method == "GET" else 1
        for i in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                    c = r.read()
                    return int(r.status), (json.loads(c) if c and parse else c)
            except urllib.error.HTTPError as e:
                return int(e.code), e.read().decode(errors="replace")
            except (http.client.IncompleteRead, urllib.error.URLError,
                    ConnectionError, TimeoutError):
                if i == attempts - 1:
                    raise
                time.sleep(1.5 * (i + 1))
        raise RuntimeError("unreachable")  # pragma: no cover

    def _expect(self, ok: tuple[int, ...], status: int, payload: Json, what: str) -> Json:
        if status not in ok:
            raise ZenodoError(f"{what} failed: HTTP {status}: {payload}")
        return payload

    # --- account / discovery --------------------------------------------
    def verify_token(self) -> tuple[bool, Json]:
        st, d = self._call("GET", "/deposit/depositions?size=1")
        return st == 200, (d if st == 200 else str(d))

    def owned_records(self, size: int = 50) -> list[dict[str, Any]]:
        st, d = self._call("GET", f"/deposit/depositions?size={size}&sort=mostrecent")
        return list(self._expect((200,), st, d, "list owned depositions"))

    def owned_records_all(self, page_size: int = 100) -> list[dict[str, Any]]:
        """Every deposition the token's account owns, across all pages. Unlike
        owned_records(), this walks the full result set so accounts with many
        records aren't silently truncated. Callers dedupe by concept as needed."""
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            st, d = self._call(
                "GET", f"/deposit/depositions?size={page_size}&page={page}&sort=mostrecent")
            self._expect((200,), st, d, "list owned depositions")
            if not d:
                break
            out.extend(d)
            if len(d) < page_size:
                break
            page += 1
        return out

    def concept_versions(self, concept_recid: str) -> list[dict[str, Any]]:
        """All published depositions in a concept, oldest->newest by created.

        Paginates: the deposit search caps size at 100, so a concept with more than
        100 versions needs every page — otherwise dedup and latest_version see only
        a slice and could miss existing records (→ duplicate archives)."""
        q = urllib.parse.quote(f"conceptrecid:{concept_recid}")
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            st, d = self._call(
                "GET",
                f"/deposit/depositions?q={q}&all_versions=true&size=100&page={page}",
            )
            self._expect((200,), st, d, "list concept versions")
            if not d:
                break
            out.extend(d)
            if len(d) < 100:
                break
            page += 1
        pub = [x for x in out if x.get("submitted")]
        pub.sort(key=lambda x: x.get("created", ""))
        return pub

    def latest_version(self, concept_recid: str) -> dict[str, Any]:
        pub = self.concept_versions(concept_recid)
        if not pub:
            raise ZenodoError(f"no published version in concept {concept_recid}")
        return max(pub, key=lambda x: x.get("created", ""))

    def get(self, dep_id: str | int) -> dict[str, Any]:
        st, d = self._call("GET", f"/deposit/depositions/{dep_id}")
        return dict(self._expect((200,), st, d, f"get deposition {dep_id}"))

    def concept_from_doi(self, doi: str | None) -> str:
        """Resolve a Zenodo DOI (concept or version) to its concept record id."""
        m = re.search(r"zenodo\.(\d+)", doi or "")
        if not m:
            raise ZenodoError(f"not a Zenodo DOI: {doi!r}")
        recid = m.group(1)
        st, d = self._call("GET", f"/records/{recid}", auth=False)
        if st == 200 and d.get("conceptrecid"):
            return str(d["conceptrecid"])
        return recid  # DOI suffix == recid for Zenodo; fall back to it

    # --- mutations -------------------------------------------------------
    def create_deposition(self, metadata: Metadata | None = None) -> dict[str, Any]:
        st, d = self._call("POST", "/deposit/depositions", data={"metadata": metadata or {}})
        return dict(self._expect((200, 201), st, d, "create deposition"))

    def new_version(self, latest_id: str | int) -> dict[str, Any]:
        st, d = self._call("POST", f"/deposit/depositions/{latest_id}/actions/newversion")
        self._expect((200, 201), st, d, "new version")
        st, draft = self._call("GET", d["links"]["latest_draft"])
        return dict(self._expect((200,), st, draft, "get new-version draft"))

    def edit(self, dep_id: str | int) -> dict[str, Any]:
        st, d = self._call("POST", f"/deposit/depositions/{dep_id}/actions/edit")
        # 400 "already editing" is fine — a draft edit already exists.
        if st not in (200, 201, 400):
            raise ZenodoError(f"edit unlock failed: HTTP {st}: {d}")
        return self.get(dep_id)

    def replace_files(
        self, draft: dict[str, Any], filepath: str, filename: str | None = None
    ) -> None:
        filename = filename or os.path.basename(filepath)
        for f in draft.get("files", []):
            self._call("DELETE", f"/deposit/depositions/{draft['id']}/files/{f['id']}")
        with open(filepath, "rb") as fh:
            st, d = self._call(
                "PUT", f"{draft['links']['bucket']}/{filename}",
                data=fh.read(), raw=True, parse=False,
            )
        self._expect((200, 201), st, d, f"upload {filename}")

    def set_metadata(self, dep_id: str | int, metadata: Metadata) -> dict[str, Any]:
        md = dict(metadata)
        md.pop("doi", None)
        md.pop("prereserve_doi", None)
        st, d = self._call("PUT", f"/deposit/depositions/{dep_id}", data={"metadata": md})
        return dict(self._expect((200,), st, d, f"update metadata {dep_id}"))

    def publish(self, dep_id: str | int) -> dict[str, Any]:
        st, d = self._call("POST", f"/deposit/depositions/{dep_id}/actions/publish")
        return dict(self._expect((202,), st, d, f"publish {dep_id}"))


# --- helpers ------------------------------------------------------------
def github_tarball(repo: str, tag: str, dest_dir: str) -> str:
    """Download a tag's source tarball from GitHub; return the local path."""
    fn = os.path.join(dest_dir, f"{repo.split('/')[-1]}-{tag}.tar.gz")
    if os.path.exists(fn) and os.path.getsize(fn) > 1000:
        return fn
    url = f"https://github.com/{repo}/archive/refs/tags/{tag}.tar.gz"
    urllib.request.urlretrieve(url, fn)
    if os.path.getsize(fn) < 1000:
        raise ZenodoError(f"tarball for {tag} is suspiciously small")
    return fn


def public_get(path: str, sandbox: bool = False) -> tuple[int, Any]:
    """Unauthenticated GET against the public records API."""
    base = SANDBOX if sandbox else PROD
    url = path if path.startswith("http") else base + path
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            return int(r.status), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode(errors="replace")


def public_concept_from_doi(doi: str | None, sandbox: bool = False) -> str:
    """Resolve a Zenodo DOI to its concept record id — public, no token."""
    m = re.search(r"zenodo\.(\d+)", doi or "")
    if not m:
        raise ZenodoError(f"not a Zenodo DOI: {doi!r}")
    recid = m.group(1)
    st, d = public_get(f"/records/{recid}", sandbox)
    if st == 200 and d.get("conceptrecid"):
        return str(d["conceptrecid"])
    return recid


def concept_ids_in_related(metadata: dict[str, Any]) -> set[str]:
    """Concept ids referenced by this record's related_identifiers (Zenodo DOIs).
    Used to auto-allow lineage links such as `continues` when detecting conflicts."""
    out: set[str] = set()
    for r in metadata.get("related_identifiers", []) or []:
        m = re.search(r"zenodo\.(\d+)", str(r.get("identifier", "")))
        if m:
            out.add(m.group(1))
    return out


def repo_from_related(metadata: dict[str, Any]) -> str | None:
    """Extract GitHub 'owner/repo' from a record's related_identifiers — the
    `isSupplementTo` github.com link the archiver sets on every version. Returns
    None if the record has no GitHub link (e.g. a non-software deposit)."""
    for r in metadata.get("related_identifiers", []) or []:
        ident = str(r.get("identifier", ""))
        idx = ident.find("github.com/")
        if idx < 0:
            continue
        path = ident[idx + len("github.com/"):].split("#")[0].split("?")[0].strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return None


def hits_total(payload: Any) -> int:
    """Number of hits in a Zenodo search response, tolerating either the legacy
    integer `hits.total` or the ElasticSearch-style `{"value": n}` shape."""
    if not isinstance(payload, dict):
        return 0
    total = payload.get("hits", {}).get("total", 0)
    if isinstance(total, dict):
        total = total.get("value", 0)
    return int(total or 0)


def public_tag_archived(concept_recid: str, tag: str, sandbox: bool = False) -> bool:
    """Is a version labeled exactly `tag` published in this concept? Public, no token.

    A single search request — deliberately NOT an enumerate-all-versions scan: the
    unauthenticated records API is rate-limited to 30 req/min, so paging a large
    concept (hundreds of versions) 429s mid-scan and would silently undercount. The
    `metadata.version:"..."` phrase match is exact (`"8.0"` does not match `8.0.0.6`),
    so one request answers the membership question precisely.

    Matches on the version *label*, which the per-release archiver sets to the source
    tag. A release captured only inside a relabeled curated rollup (label != tag)
    would not be found — acceptable: the archiver's default labels a record by its
    tag, and erring toward flagging drift is the intended loud-on-failure behavior."""
    q = urllib.parse.quote(f'conceptrecid:{concept_recid} AND metadata.version:"{tag}"')
    st, d = public_get(f"/records?q={q}&all_versions=true&size=1", sandbox)
    if st != 200:
        raise ZenodoError(f"could not query concept {concept_recid}: HTTP {st}: {d}")
    return hits_total(d) > 0


def latest_release_archived(
    repo: str, concept_recid: str, sandbox: bool = False
) -> tuple[bool, str]:
    """Drift check: is the repo's latest GitHub release archived in the concept?
    Returns (archived, gh_tag); public, no token.

    Membership — not equality against the single 'latest' Zenodo version — because a
    backfill of old releases (or a curated major rollup) creates records today, so the
    newest-by-`created` version no longer tracks release chronology and can lag the
    real latest release. 'Is the latest release's tag archived?' is the robust
    question, and public_tag_archived answers it in one request."""
    gh_tag, _ = latest_github_release(repo)
    return public_tag_archived(concept_recid, gh_tag, sandbox), gh_tag


def _identifier_is_repo(identifier: str, repo: str) -> bool:
    """True if a related-identifier URL points at exactly github.com/<repo> (not a
    different repo that merely shares a prefix, e.g. vcell vs vcell-solvers)."""
    s = str(identifier)
    idx = s.find("github.com/")
    if idx < 0:
        return False
    path = s[idx + len("github.com/"):].split("#")[0].split("?")[0]
    if path.endswith(".git"):
        path = path[:-4]
    return path == repo or path.startswith(repo + "/")


def concepts_referencing_repo(repo: str, sandbox: bool = False) -> set[str]:
    """Published Zenodo concepts whose records point at github.com/<repo> in their
    related_identifiers — public, no token. Best-effort (wildcard search on the repo
    name + a precise, boundary-aware client-side filter). Returns an empty set on
    any search failure so callers never treat 'could not check' as a conflict."""
    term = repo.split("/")[-1]  # no slashes/quotes in the query — those 500 the search
    concepts: set[str] = set()
    try:
        q = urllib.parse.quote(f"related.identifier:*{term}*")
        st, d = public_get(f"/records?q={q}&size=25", sandbox)  # 25 = public max
    except Exception:
        return set()
    if st != 200 or not isinstance(d, dict):
        return set()
    for h in d.get("hits", {}).get("hits", []):
        rels = h.get("metadata", {}).get("related_identifiers", []) or []
        if any(_identifier_is_repo(str(r.get("identifier", "")), repo) for r in rels):
            cid = h.get("conceptrecid")
            if cid:
                concepts.add(str(cid))
    return concepts


def github_webhooks(repo: str, token: str) -> list[dict[str, Any]]:
    """Repo webhooks (needs a token with repo-admin). Raises on error."""
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/hooks",
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return list(json.loads(r.read()))


def _gh(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
        return dict(json.loads(r.read()))


def latest_github_release(repo: str) -> tuple[str, str]:
    """Latest published (non-prerelease) release tag; public API, no auth."""
    d = _gh(f"https://api.github.com/repos/{repo}/releases/latest")
    return d.get("tag_name", ""), (d.get("published_at") or "")[:10]


def github_release_date(repo: str, tag: str) -> str:
    """Publication date (YYYY-MM-DD) of a specific release tag."""
    d = _gh(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    return (d.get("published_at") or "")[:10]


def _github_headers(token: str | None, accept: str) -> dict[str, str]:
    h = {"Accept": accept}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def github_file(repo: str, path: str, token: str | None = None) -> str | None:
    """Raw contents of a file on a repo's default branch, or None if it's absent
    (404). Public; a token is optional but recommended — the unauthenticated GitHub
    API allows only 60 requests/hour, which a multi-repo audit blows through fast."""
    url = f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}"
    req = urllib.request.Request(
        url, headers=_github_headers(token, "application/vnd.github.raw"))
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            data: bytes = r.read()
            return data.decode(errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def github_dir(repo: str, path: str, token: str | None = None) -> list[str]:
    """Filenames directly under a repo directory (default branch); [] if the
    directory is absent. Public; token optional (see github_file on rate limits)."""
    url = f"https://api.github.com/repos/{repo}/contents/{urllib.parse.quote(path)}"
    req = urllib.request.Request(
        url, headers=_github_headers(token, "application/vnd.github+json"))
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    return [str(x.get("name", "")) for x in data] if isinstance(data, list) else []


_REUSABLE_WORKFLOW_RE = re.compile(
    r"zenodo-maint/\.github/workflows/(archive|drift)\.reusable\.yml@(\S+)")


def reusable_refs(workflow_texts: Iterable[str]) -> dict[str, str | None]:
    """The git ref each zenodo-maint reusable workflow is pinned at, across the given
    workflow file contents: {'archive': ref|None, 'drift': ref|None}. None means the
    repo doesn't call that reusable workflow at all. Pure — no I/O."""
    out: dict[str, str | None] = {"archive": None, "drift": None}
    for text in workflow_texts:
        for kind, ref in _REUSABLE_WORKFLOW_RE.findall(text):
            out[kind] = ref.strip().strip("\"'")
    return out


def is_major_pin(ref: str | None) -> bool:
    """True if a workflow ref is a floating major tag (vN) — the only pin that keeps
    tracking minor/patch updates. A full vX.Y.Z, a commit SHA, or a branch does not."""
    return ref is not None and re.fullmatch(r"v\d+", ref) is not None


def with_lineage(
    related: list[dict[str, Any]] | None, continues_doi: str | None, repo: str, tag: str
) -> list[dict[str, Any]]:
    """Return related_identifiers with the per-tag GitHub supplement link set and,
    if given and not already present, the `continues` lineage link — preserving any
    other entries carried in from .zenodo.json or the inherited version."""
    rels = [
        r for r in (related or [])
        if not (r.get("relation") == "isSupplementTo"
                and "github.com" in str(r.get("identifier", "")))
    ]
    if continues_doi and not any(r.get("relation") == "continues" for r in rels):
        rels.insert(0, {"relation": "continues", "identifier": continues_doi, "scheme": "doi"})
    rels.append({
        "relation": "isSupplementTo",
        "identifier": f"https://github.com/{repo}/tree/{tag}",
        "scheme": "url",
    })
    return rels
