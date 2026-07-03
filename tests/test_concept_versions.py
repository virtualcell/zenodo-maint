"""Unit tests for ZenodoClient.concept_versions' resilience to Zenodo's unstable,
overlapping deposit-search pagination — a single pass can return a record twice
and miss others, so the method unions records by id across repeated passes until
the set is complete (by the public-API total, or a converged pass as fallback).

Network is stubbed: _call yields canned pages and public_get a canned total.
Stdlib `unittest` only. Run with:  python -m unittest discover -s tests
"""
import unittest

from zenodo_maint import api


def _rec(i: int) -> dict:
    return {"id": i, "submitted": True, "created": f"2020-{i:02d}-01",
            "metadata": {"version": str(i)}}


class ConceptVersions(unittest.TestCase):
    def _client(self, pages: list) -> api.ZenodoClient:
        cli = api.ZenodoClient("dummy-token")
        it = iter(pages)

        def fake_call(method: str, path: str, *a: object, **k: object) -> tuple:
            try:
                return (200, next(it))
            except StopIteration:
                return (200, [])

        cli._call = fake_call  # type: ignore[method-assign]
        return cli

    def _run(self, pages: list, total: int) -> list:
        cli = self._client(pages)
        orig = api.public_get
        api.public_get = lambda p, s=False: (200, {"hits": {"total": total}})
        try:
            return [x["id"] for x in cli.concept_versions("123")]
        finally:
            api.public_get = orig

    def test_unions_overlapping_pages_and_dedups(self) -> None:
        # no total available (0) -> rely on a converged (nothing-new) pass; each pass
        # returns a different, overlapping 2-of-3 slice.
        pages = [[_rec(1), _rec(2)], [_rec(2), _rec(3)], [_rec(1), _rec(3)]]
        self.assertEqual(self._run(pages, total=0), [1, 2, 3])

    def test_stops_once_target_reached(self) -> None:
        # with a known total, stop as soon as the union reaches it (later pages unused)
        pages = [[_rec(1), _rec(2)], [_rec(2), _rec(3)], [_rec(1)], [_rec(1)]]
        self.assertEqual(self._run(pages, total=3), [1, 2, 3])

    def test_sorted_oldest_first(self) -> None:
        pages = [[_rec(3), _rec(1)], [_rec(2)], []]
        self.assertEqual(self._run(pages, total=0), [1, 2, 3])

    def test_excludes_unsubmitted_drafts(self) -> None:
        draft = {"id": 9, "created": "2021-01-01", "metadata": {}}  # no "submitted"
        self.assertEqual(self._run([[_rec(1), draft]], total=0), [1])


if __name__ == "__main__":
    unittest.main()
