"""Unit tests for the version-label precedence used by archive/backfill.

Stdlib `unittest` only (the package is dependency-free by design). Run with:

    python -m unittest discover -s tests
"""
import unittest

from zenodo_maint.cli import _effective_version, _skip_reason


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


class SkipReason(unittest.TestCase):
    # existing is keyed by version label; values are opaque records here
    EX = {"7.7.0.15": {"id": 1}, "8.0": {"id": 2}}

    def test_matching_label_skips_in_both_modes(self) -> None:
        for mode in ("tag", "label"):
            self.assertEqual(_skip_reason("8.0.0.06", "8.0", self.EX, mode), "label")

    def test_tag_mode_skips_when_only_tag_exists(self) -> None:
        # curated 7.7 reusing already-archived tag 7.7.0.15 -> conservative skip
        self.assertEqual(_skip_reason("7.7.0.15", "7.7", self.EX, "tag"), "tag")

    def test_label_mode_allows_tag_reuse(self) -> None:
        # same case, label mode -> not skipped (curated record may reuse the tag)
        self.assertIsNone(_skip_reason("7.7.0.15", "7.7", self.EX, "label"))

    def test_plain_archive_skips_when_tag_equals_label_exists(self) -> None:
        # normal flow: label == tag; a re-run is a safe no-op in both modes
        for mode in ("tag", "label"):
            self.assertEqual(_skip_reason("8.0", "8.0", self.EX, mode), "label")

    def test_new_record_not_skipped(self) -> None:
        for mode in ("tag", "label"):
            self.assertIsNone(_skip_reason("9.9.9", "9.9.9", self.EX, mode))


if __name__ == "__main__":
    unittest.main()
