"""Read the two standard repo files — no third-party deps.

- .zenodo.json : Zenodo deposit metadata (JSON) — the deposit metadata source.
                 Generate from CITATION.cff via `cffconvert -f zenodo`, then add
                 any `related_identifiers` (e.g. the `continues` lineage link).
- CITATION.cff : the human citation source of truth. We only need two facts from
                 it operationally — the concept DOI and the repo — parsed with a
                 light line scan (no YAML dependency).
"""
import json
import os
import re


def read_zenodo_json(path='.zenodo.json'):
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        data = json.load(fh)
    data.pop('//', None)  # allow a comment key in the example
    return data


def citation_doi(path='CITATION.cff'):
    """Top-level `doi:` from CITATION.cff (the concept DOI). Column-0 only, so the
    nested doi under `identifiers:` is ignored."""
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        for line in fh:
            m = re.match(r'''doi:\s*['"]?([^'"\s#]+)''', line)
            if m:
                return m.group(1)
    return None


def citation_repo(path='CITATION.cff'):
    """An `owner/repo` found in CITATION.cff (e.g. repository-code)."""
    if not path or not os.path.exists(path):
        return None
    with open(path) as fh:
        text = fh.read()
    m = re.search(r'github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git|["\'\s/]|$)', text)
    return m.group(1) if m else None
