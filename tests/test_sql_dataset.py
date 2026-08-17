"""Dataset-backed SQL judging, against real SQLite files.

These run the harness in-process (no docker), so they cover the part that decides a
verdict: which database each test opens, and whether a wrong answer is actually caught.

The scenario is the real one from the commerce dataset — a report that must include rows
with no matching data. An INNER JOIN passes on the variant the student can see and fails
on one they cannot, which is the entire reason hidden variants exist. A test suite that
only proved "correct answer accepted" would not notice if variant selection broke and
every case silently ran against v0.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "judge"))
from harness import run_sql  # noqa: E402

from judge.datasets import variants_needed  # noqa: E402

SCHEMA = """
CREATE TABLE cities (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE orders (id INTEGER PRIMARY KEY, city_id INTEGER NOT NULL REFERENCES cities(id));
"""
# v0: every city has an order.        v9: 'Kochi' has none — the planted anomaly.
SEEDS = {
    "v0": "INSERT INTO cities VALUES (1,'Delhi'),(2,'Kochi');"
          "INSERT INTO orders VALUES (1,1),(2,1),(3,2);",
    "v9": "INSERT INTO cities VALUES (1,'Delhi'),(2,'Kochi');"
          "INSERT INTO orders VALUES (1,1),(2,1);",
}
CORRECT = ("SELECT c.name AS city, COUNT(o.id) AS orders FROM cities c "
           "LEFT JOIN orders o ON o.city_id = c.id GROUP BY c.id ORDER BY c.name")
WRONG = ("SELECT c.name AS city, COUNT(o.id) AS orders FROM cities c "
         "JOIN orders o ON o.city_id = c.id GROUP BY c.id ORDER BY c.name")


def _build(tmp: str) -> dict[str, str]:
    paths = {}
    for vid, seed in SEEDS.items():
        p = os.path.join(tmp, f"commerce.{vid}.db")
        con = sqlite3.connect(p)
        con.executescript(SCHEMA)
        con.executescript(seed)
        con.commit()
        con.close()
        paths[vid] = p
    return paths


def _work(code: str, datasets: dict[str, str], mode: str = "exact") -> dict:
    return {
        "code": code,
        "compareMode": mode,
        "timeLimitMs": 5000,
        "datasets": datasets,
        "tests": [
            {"input": ["v0"], "expected": {"columns": ["city", "orders"],
                                           "rows": [["Delhi", 2], ["Kochi", 1]]}},
            {"input": ["v9"], "expected": {"columns": ["city", "orders"],
                                           "rows": [["Delhi", 2], ["Kochi", 0]]}},
        ],
    }


class TestDatasetJudging(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.paths = _build(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_correct_solution_passes_every_variant(self) -> None:
        out = run_sql(_work(CORRECT, self.paths))
        self.assertEqual(out["verdict"], "AC", out.get("error"))
        self.assertEqual(out["passed"], 2)

    def test_wrong_solution_passes_the_visible_variant_and_fails_a_hidden_one(self) -> None:
        """The point of the whole design: green in the browser, still rejected here."""
        out = run_sql(_work(WRONG, self.paths))
        self.assertEqual(out["verdict"], "WA")
        self.assertEqual(out["passed"], 1)      # v0 passed …
        self.assertEqual(out["failedCase"], 1)  # … v9 did not

    def test_each_test_opens_its_own_variant(self) -> None:
        """Guards the failure that would be invisible: every case running against v0."""
        work = _work(CORRECT, self.paths)
        work["tests"] = [work["tests"][1]]  # v9 only
        self.assertEqual(run_sql(work)["verdict"], "AC")

    def test_missing_variant_is_reported_not_blamed_on_the_student(self) -> None:
        out = run_sql(_work(CORRECT, {"v0": self.paths["v0"]}))
        self.assertEqual(out["verdict"], "RE")
        self.assertIn("v9", out["error"])

    def test_database_is_read_only(self) -> None:
        out = run_sql(_work("DELETE FROM orders", self.paths))
        self.assertIn(out["verdict"], ("CE", "RE"))
        self.assertIn("readonly", out["error"].lower())

    def test_attach_is_denied(self) -> None:
        """Otherwise a submission could read a variant it is not being judged on."""
        out = run_sql(_work(f"ATTACH '{self.paths['v9']}' AS other; SELECT 1", self.paths))
        self.assertIn(out["verdict"], ("CE", "RE"))

    def test_variants_needed_dedups_and_preserves_order(self) -> None:
        tests = [{"input": ["v0"]}, {"input": ["v9"]}, {"input": ["v0"]}]
        self.assertEqual(variants_needed(tests), ["v0", "v9"])


if __name__ == "__main__":
    unittest.main()
