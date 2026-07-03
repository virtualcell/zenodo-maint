"""Unit tests for api.repo_from_related — the GitHub owner/repo extractor used by
`list-owned` to map an owned Zenodo concept back to its source repository.

Stdlib `unittest` only. Run with:  python -m unittest discover -s tests
"""
import unittest

from zenodo_maint.api import repo_from_related


class RepoFromRelated(unittest.TestCase):
    def _md(self, *identifiers):
        return {"related_identifiers": [
            {"relation": "isSupplementTo", "identifier": i, "scheme": "url"}
            for i in identifiers
        ]}

    def test_tree_tag_url(self) -> None:
        md = self._md("https://github.com/virtualcell/vcell/tree/v8.0")
        self.assertEqual(repo_from_related(md), "virtualcell/vcell")

    def test_bare_repo_url(self) -> None:
        self.assertEqual(repo_from_related(self._md("https://github.com/org/repo")), "org/repo")

    def test_git_suffix_stripped(self) -> None:
        self.assertEqual(repo_from_related(self._md("https://github.com/org/repo.git")), "org/repo")

    def test_only_owner_repo_segments(self) -> None:
        # deeper paths must not leak into the repo id
        md = self._md("https://github.com/biosimulators/Biosimulators_utils/releases/tag/0.2.3")
        self.assertEqual(repo_from_related(md), "biosimulators/Biosimulators_utils")

    def test_picks_github_among_others(self) -> None:
        md = {"related_identifiers": [
            {"relation": "continues", "identifier": "10.5281/zenodo.123", "scheme": "doi"},
            {"relation": "isSupplementTo",
             "identifier": "https://github.com/cam-center/SpringSaLaD/tree/2.4.6", "scheme": "url"},
        ]}
        self.assertEqual(repo_from_related(md), "cam-center/SpringSaLaD")

    def test_none_when_no_github(self) -> None:
        md = {"related_identifiers": [
            {"relation": "continues", "identifier": "10.5281/zenodo.123", "scheme": "doi"}]}
        self.assertIsNone(repo_from_related(md))

    def test_none_on_empty(self) -> None:
        self.assertIsNone(repo_from_related({}))
        self.assertIsNone(repo_from_related({"related_identifiers": None}))


if __name__ == "__main__":
    unittest.main()
