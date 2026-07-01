"""Resolve settings from (in precedence order) CLI args > env > config file > default.

Config file (``zenodo.toml``, discovered in the cwd or via --config) declares a
repo's Zenodo facts so they live *in the repo*, versioned and reviewable:

    concept_recid = "21053715"
    continues_doi = "10.5281/zenodo.5057108"
    repo          = "biosimulations/biosimulations"
    authors_file  = "authors.json"     # optional: list of {name, orcid, affiliation}
"""
import os

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

DEFAULT_TOKEN_PATHS = ['~/.ssh/zenodo-token', '~/.config/zenodo-token']


def load_file(path=None):
    if path is None:
        path = 'zenodo.toml' if os.path.exists('zenodo.toml') else None
    if not path:
        return {}
    if tomllib is None:
        raise RuntimeError('reading a config file needs Python 3.11+ (tomllib)')
    with open(path, 'rb') as fh:
        return tomllib.load(fh)


def resolve(cli_val, env_key, file_cfg, key, default=None):
    if cli_val is not None:
        return cli_val
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    if key in file_cfg:
        return file_cfg[key]
    return default


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
