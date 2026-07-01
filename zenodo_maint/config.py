"""Token resolution. Operational facts (concept, repo) now come from the standard
repo files via ``sources`` (CITATION.cff / .zenodo.json), not a bespoke config."""
from __future__ import annotations

import os

DEFAULT_TOKEN_PATHS = ["~/.ssh/zenodo-token", "~/.config/zenodo-token"]


def read_token(explicit_file: str | None = None) -> str | None:
    """Token from --token-file, then $ZENODO_TOKEN, then the default paths."""
    if explicit_file:
        with open(os.path.expanduser(explicit_file)) as fh:
            return fh.read().strip()
    if os.environ.get("ZENODO_TOKEN"):
        return os.environ["ZENODO_TOKEN"].strip()
    for p in DEFAULT_TOKEN_PATHS:
        expanded = os.path.expanduser(p)
        if os.path.exists(expanded):
            with open(expanded) as fh:
                return fh.read().strip()
    return None
