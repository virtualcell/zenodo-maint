"""Token resolution. Operational facts (concept, repo) now come from the standard
repo files via ``sources`` (CITATION.cff / .zenodo.json), not a bespoke config."""
import os

DEFAULT_TOKEN_PATHS = ['~/.ssh/zenodo-token', '~/.config/zenodo-token']


def read_token(explicit_file=None):
    """Token from --token-file, then $ZENODO_TOKEN, then the default paths."""
    if explicit_file:
        return open(os.path.expanduser(explicit_file)).read().strip()
    if os.environ.get('ZENODO_TOKEN'):
        return os.environ['ZENODO_TOKEN'].strip()
    for p in DEFAULT_TOKEN_PATHS:
        p = os.path.expanduser(p)
        if os.path.exists(p):
            return open(p).read().strip()
    return None
