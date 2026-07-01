"""Read the two standard repo files — no third-party deps.

- .zenodo.json : Zenodo deposit metadata (JSON) — the deposit metadata source.
                 Generate from CITATION.cff via `cffconvert -f zenodo`, then add
                 any `related_identifiers` (e.g. the `continues` lineage link).
- CITATION.cff : the human citation source of truth. We only need two facts from
                 it operationally — the concept DOI and the repo — parsed with a
                 light line scan (no YAML dependency).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any


def read_zenodo_json(path: str = ".zenodo.json") -> dict[str, Any] | None:
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        data: dict[str, Any] = json.load(fh)
    data.pop("//", None)  # allow a comment key in the example
    return data


def citation_doi(path: str = "CITATION.cff") -> str | None:
    """Top-level `doi:` from CITATION.cff (the concept DOI). Column-0 only, so the
    nested doi under `identifiers:` is ignored."""
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        for line in fh:
            m = re.match(r"""doi:\s*['"]?([^'"\s#]+)""", line)
            if m:
                return m.group(1)
    return None


def citation_repo(path: str = "CITATION.cff") -> str | None:
    """An `owner/repo` found in CITATION.cff (e.g. repository-code)."""
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        text = fh.read()
    m = re.search(r"github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git|[\"'\s/]|$)", text)
    return m.group(1) if m else None
