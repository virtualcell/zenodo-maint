"""Unit tests for --execute placement on the CLI parser.

--execute is a main-parser option, but the README, the skill, and the reusable
archive workflow all write it after the subcommand (`archive-release --tag X
--execute`). Both positions must parse, and omitting it must stay dry-run.

Stdlib `unittest` only (the package is dependency-free by design). Run with:

    python -m unittest discover -s tests
"""
import unittest

from zenodo_maint.cli import build_parser

MUTATING = [
    ["archive-release", "--tag", "v1.0"],
    ["create-concept", "--tag", "v1.0"],
    ["backfill", "--tags-file", "tags.json"],
    ["relink"],
    ["apply-metadata"],
]


class ExecutePlacement(unittest.TestCase):
    def test_trailing_execute(self) -> None:
        for argv in MUTATING:
            with self.subTest(cmd=argv[0]):
                ns = build_parser().parse_args([*argv, "--execute"])
                self.assertTrue(ns.execute)

    def test_leading_execute(self) -> None:
        for argv in MUTATING:
            with self.subTest(cmd=argv[0]):
                ns = build_parser().parse_args(["--execute", *argv])
                self.assertTrue(ns.execute)

    def test_both_positions(self) -> None:
        ns = build_parser().parse_args(
            ["--execute", "archive-release", "--tag", "v1.0", "--execute"])
        self.assertTrue(ns.execute)

    def test_omitted_means_dry_run(self) -> None:
        for argv in MUTATING:
            with self.subTest(cmd=argv[0]):
                ns = build_parser().parse_args(argv)
                self.assertFalse(ns.execute)


if __name__ == "__main__":
    unittest.main()
