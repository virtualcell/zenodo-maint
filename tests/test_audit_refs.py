"""Unit tests for the pure workflow-pinning logic behind `audit`:
api.reusable_refs (which ref each reusable workflow is pinned at) and
api.is_major_pin (is it the floating vN that tracks updates?).

Stdlib `unittest` only. Run with:  python -m unittest discover -s tests
"""
import unittest

from zenodo_maint.api import is_major_pin, reusable_refs

ARCHIVE = "uses: virtualcell/zenodo-maint/.github/workflows/archive.reusable.yml@v1"
DRIFT = "uses: virtualcell/zenodo-maint/.github/workflows/drift.reusable.yml@v1"


class ReusableRefs(unittest.TestCase):
    def test_both_across_separate_files(self) -> None:
        self.assertEqual(reusable_refs([ARCHIVE, DRIFT]),
                         {"archive": "v1", "drift": "v1"})

    def test_only_archive(self) -> None:
        self.assertEqual(reusable_refs([ARCHIVE]), {"archive": "v1", "drift": None})

    def test_none_referenced(self) -> None:
        self.assertEqual(reusable_refs(["uses: actions/checkout@v4\n"]),
                         {"archive": None, "drift": None})

    def test_trailing_comment_not_captured(self) -> None:
        # the real callers carry a NOSONAR comment after the ref
        line = ARCHIVE + " # NOSONAR(S7637): first-party org action; intentional @v1"
        self.assertEqual(reusable_refs([line])["archive"], "v1")

    def test_pinned_full_version(self) -> None:
        line = "uses: virtualcell/zenodo-maint/.github/workflows/archive.reusable.yml@v1.6.0"
        self.assertEqual(reusable_refs([line])["archive"], "v1.6.0")

    def test_quotes_stripped(self) -> None:
        line = 'uses: "virtualcell/zenodo-maint/.github/workflows/drift.reusable.yml@v2"'
        self.assertEqual(reusable_refs([line])["drift"], "v2")


class IsMajorPin(unittest.TestCase):
    def test_major_tags(self) -> None:
        self.assertTrue(is_major_pin("v1"))
        self.assertTrue(is_major_pin("v2"))
        self.assertTrue(is_major_pin("v12"))

    def test_not_major(self) -> None:
        for ref in ["v1.6.0", "v1.6", "1", "main", "abc1234", "v1beta", "vv1", "", None]:
            self.assertFalse(is_major_pin(ref), ref)


if __name__ == "__main__":
    unittest.main()
