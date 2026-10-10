"""Unit tests for the rollup-tolerant drift check (api.oldest_unarchived_since_last_archived
and api.release_lag). Network is stubbed. Stdlib `unittest` only. Run with:
python -m unittest discover -s tests
"""
import datetime
import unittest
from unittest import mock

from zenodo_maint import api

RELEASES = [("8.2.0.16", "2026-10-09"), ("8.2.0.15", "2026-10-05"),
            ("8.1.8.06", "2026-10-01"), ("8.2.0.14", "2026-09-20"), ("8.2.0.13", "2026-09-01")]


class OldestUnarchived(unittest.TestCase):
    def test_latest_archived_is_in_sync(self) -> None:
        seen: list[str] = []

        def archived(tag: str) -> bool:
            seen.append(tag)
            return True

        self.assertIsNone(api.oldest_unarchived_since_last_archived(RELEASES, archived))
        self.assertEqual(seen, ["8.2.0.16"])  # one membership query when in sync

    def test_returns_the_release_just_after_the_last_archived(self) -> None:
        got = api.oldest_unarchived_since_last_archived(RELEASES, lambda t: t == "8.2.0.14")
        self.assertEqual(got, ("8.1.8.06", "2026-10-01"))

    def test_stops_at_the_first_release_past_the_cutoff(self) -> None:
        seen: list[str] = []

        def archived(tag: str) -> bool:
            seen.append(tag)
            return False

        got = api.oldest_unarchived_since_last_archived(RELEASES, archived, not_before="2026-10-03")
        self.assertEqual(got, ("8.1.8.06", "2026-10-01"))
        self.assertEqual(seen, ["8.2.0.16", "8.2.0.15", "8.1.8.06"])


class ReleaseLag(unittest.TestCase):
    def _lag(self, archived_tags: set[str], days: int) -> tuple:
        def archived(concept: str, tag: str, sandbox: bool = False) -> bool:
            return tag in archived_tags

        def archived_many(concept: str, tags: list[str], sandbox: bool = False) -> set[str]:
            return {t for t in tags if t in archived_tags}

        with mock.patch.object(api, "github_releases", return_value=RELEASES), \
                mock.patch.object(api, "public_tag_archived", archived), \
                mock.patch.object(api, "public_tags_archived", archived_many):
            return api.release_lag("o/r", "1", days, today=datetime.date(2026, 10, 10))

    def test_recent_releases_waiting_for_a_rollup_are_not_drift(self) -> None:
        latest, waiting, drifted = self._lag({"8.2.0.14"}, 35)
        self.assertEqual(latest, "8.2.0.16")
        self.assertEqual(waiting, ("8.1.8.06", "2026-10-01"))
        self.assertFalse(drifted)

    def test_a_release_waiting_past_the_tolerance_is_drift(self) -> None:
        # The walk stops at the first release past the 7-day cutoff: it proves drift.
        _, waiting, drifted = self._lag({"8.2.0.13"}, 7)
        self.assertEqual(waiting, ("8.1.8.06", "2026-10-01"))
        self.assertTrue(drifted)

    def test_in_sync(self) -> None:
        self.assertEqual(self._lag({"8.2.0.16"}, 35), ("8.2.0.16", None, False))

    def test_no_releases(self) -> None:
        with mock.patch.object(api, "github_releases", return_value=[]):
            self.assertEqual(api.release_lag("o/r", "1", 35), ("", None, False))


if __name__ == "__main__":
    unittest.main()
