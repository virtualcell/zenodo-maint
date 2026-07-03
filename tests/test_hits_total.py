"""Unit tests for api.hits_total — the search-response total parser behind the
membership-based drift check.

check-drift/doctor now ask "is the latest GitHub release's tag archived in this
concept?" via a single `metadata.version:"<tag>"` search (public_tag_archived),
rather than enumerating every version — the unauthenticated records API is capped
at 30 req/min and paging a large concept 429s mid-scan. hits_total normalizes the
`hits.total` field, which Zenodo returns as a bare int (legacy) or an
ElasticSearch-style {"value": n}.

Stdlib `unittest` only. Run with:  python -m unittest discover -s tests
"""
import unittest

from zenodo_maint.api import hits_total


class HitsTotal(unittest.TestCase):
    def test_legacy_int_total(self) -> None:
        self.assertEqual(hits_total({"hits": {"total": 3}}), 3)

    def test_zero(self) -> None:
        self.assertEqual(hits_total({"hits": {"total": 0}}), 0)

    def test_elasticsearch_value_shape(self) -> None:
        self.assertEqual(hits_total({"hits": {"total": {"value": 5}}}), 5)

    def test_missing_hits(self) -> None:
        self.assertEqual(hits_total({}), 0)

    def test_missing_total(self) -> None:
        self.assertEqual(hits_total({"hits": {}}), 0)

    def test_non_dict_payload(self) -> None:
        # public_get returns a decoded error string on HTTP errors
        self.assertEqual(hits_total("A validation error occurred."), 0)
        self.assertEqual(hits_total(None), 0)


if __name__ == "__main__":
    unittest.main()
