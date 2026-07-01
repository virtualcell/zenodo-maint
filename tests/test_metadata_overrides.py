"""Unit tests for the version-label precedence used by archive/backfill.

Stdlib `unittest` only (the package is dependency-free by design). Run with:

    python -m unittest discover -s tests
"""
import unittest

from zenodo_maint.cli import _effective_version


class EffectiveVersion(unittest.TestCase):
    def test_defaults_to_tag_when_no_file_and_no_override(self) -> None:
        # native archive path: inherited metadata carries the *previous* version,
        # which must be ignored so the record is labeled by its own tag.
        base = {"version": "9.9.9-previous"}
        self.assertEqual(_effective_version(None, base, False, "v1.2.3"), "v1.2.3")

    def test_file_version_wins_over_tag(self) -> None:
        base = {"version": "7.7"}
        self.assertEqual(_effective_version(None, base, True, "7.7.0.33"), "7.7")

    def test_file_without_version_falls_back_to_tag(self) -> None:
        self.assertEqual(_effective_version(None, {}, True, "7.7.0.33"), "7.7.0.33")

    def test_explicit_override_beats_everything(self) -> None:
        base = {"version": "7.7"}
        self.assertEqual(_effective_version("8.0", base, True, "7.7.0.33"), "8.0")
        self.assertEqual(_effective_version("8.0", {}, False, "7.7.0.33"), "8.0")


if __name__ == "__main__":
    unittest.main()
