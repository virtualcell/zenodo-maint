"""replace_files retries a timed-out upload (an idempotent PUT to a bucket key) with the
upload timeout, while other writes stay single-attempt. Network is stubbed.
Stdlib `unittest` only. Run with:  python -m unittest discover -s tests
"""
import unittest
from unittest import mock

from zenodo_maint import api


class _Resp:
    status = 201

    def __enter__(self) -> "_Resp":
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def read(self) -> bytes:
        return b""


class UploadRetry(unittest.TestCase):
    def test_upload_retries_with_the_upload_timeout(self) -> None:
        calls: list[tuple[str, float]] = []

        def fake_urlopen(req: object, timeout: float) -> _Resp:
            calls.append((getattr(req, "method", ""), timeout))
            if len(calls) == 1:
                raise TimeoutError("The write operation timed out")
            return _Resp()

        cli = api.ZenodoClient("t")
        with mock.patch.object(api.urllib.request, "urlopen", fake_urlopen), \
                mock.patch.object(api.time, "sleep"), \
                mock.patch("builtins.open", mock.mock_open(read_data=b"tar")):
            cli.replace_files({"id": 1, "files": [], "links": {"bucket": "https://b/x"}}, "/x.tgz")
        self.assertEqual(calls, [("PUT", api.UPLOAD_TIMEOUT), ("PUT", api.UPLOAD_TIMEOUT)])

    def test_other_writes_are_not_retried(self) -> None:
        cli = api.ZenodoClient("t")

        def boom(req: object, timeout: float) -> _Resp:
            raise TimeoutError("timed out")

        with mock.patch.object(api.urllib.request, "urlopen", boom) as _, \
                self.assertRaises(TimeoutError):
            cli._call("POST", "https://z/actions/publish")


if __name__ == "__main__":
    unittest.main()
