#!/usr/bin/env python3
"""Sandbox smoke test — exercise the full Zenodo deposit flow against
sandbox.zenodo.org so an API-breaking change surfaces in CI before it hits
production. Covers: create, bucket upload, set metadata, publish, new version
(inherit + swap file), and edit-a-published-record.

Needs $ZENODO_SANDBOX_TOKEN (a token from sandbox.zenodo.org). Skips (exit 0)
if the token is absent, so the job is a no-op until the secret is configured.
"""
import os
import sys
import tempfile

from zenodo_maint import api

META = {
    "upload_type": "software",
    "title": "zenodo-maint smoke test",
    "description": "Automated API smoke test — safe to delete.",
    "creators": [{"name": "CI, Smoke"}],
}


def main() -> int:
    token = os.environ.get("ZENODO_SANDBOX_TOKEN", "").strip()
    if not token:
        print("SKIP: no ZENODO_SANDBOX_TOKEN set")
        return 0
    cli = api.ZenodoClient(token, sandbox=True)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "smoke.txt")

        with open(path, "w") as fh:
            fh.write("v1")
        dep = cli.create_deposition(META)
        cli.replace_files(dep, path)
        cli.set_metadata(dep["id"], {**META, "version": "v1", "publication_date": "2026-01-01"})
        v1 = cli.publish(dep["id"])
        print("published v1:", v1.get("doi"))

        draft = cli.new_version(dep["id"])
        with open(path, "w") as fh:
            fh.write("v2")
        cli.replace_files(draft, path)
        md = dict(draft["metadata"])
        md["version"] = "v2"
        md["publication_date"] = "2026-02-02"
        cli.set_metadata(draft["id"], md)
        v2 = cli.publish(draft["id"])
        print("published v2:", v2.get("doi"))

        cli.edit(v2["id"])
        edited = cli.get(v2["id"])["metadata"]
        edited["description"] = "Automated API smoke test — edited."
        cli.set_metadata(v2["id"], edited)
        cli.publish(v2["id"])
        print("edited + republished v2")

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
