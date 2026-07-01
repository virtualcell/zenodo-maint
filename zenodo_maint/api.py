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

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

PROD = "https://zenodo.org/api"
SANDBOX = "https://sandbox.zenodo.org/api"

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
        try:
            with urllib.request.urlopen(req) as r:
                c = r.read()
                return int(r.status), (json.loads(c) if c and parse else c)
        except urllib.error.HTTPError as e:
            return int(e.code), e.read().decode(errors="replace")

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

    def concept_versions(self, concept_recid: str) -> list[dict[str, Any]]:
        """All published depositions in a concept, oldest->newest by created."""
        q = urllib.parse.quote(f"conceptrecid:{concept_recid}")
        st, d = self._call("GET", f"/deposit/depositions?q={q}&all_versions=true&size=100")
        self._expect((200,), st, d, "list concept versions")
        pub = [x for x in d if x.get("submitted")]
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
        with urllib.request.urlopen(req) as r:
            return int(r.status), json.loads(r.read())
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode(errors="replace")


def public_latest_version(concept_recid: str, sandbox: bool = False) -> str | None:
    """Latest published version string for a concept — public, no token. Fetching
    the concept record id follows redirects to the latest version's record."""
    st, d = public_get(f"/records/{concept_recid}", sandbox)
    if st != 200:
        raise ZenodoError(f"could not read record {concept_recid}: HTTP {st}: {d}")
    version: str | None = d.get("metadata", {}).get("version")
    return version


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


def _gh(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req) as r:
        return dict(json.loads(r.read()))


def latest_github_release(repo: str) -> tuple[str, str]:
    """Latest published (non-prerelease) release tag; public API, no auth."""
    d = _gh(f"https://api.github.com/repos/{repo}/releases/latest")
    return d.get("tag_name", ""), (d.get("published_at") or "")[:10]


def github_release_date(repo: str, tag: str) -> str:
    """Publication date (YYYY-MM-DD) of a specific release tag."""
    d = _gh(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")
    return (d.get("published_at") or "")[:10]


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
