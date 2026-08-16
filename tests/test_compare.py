"""The comparison rules are the one thing that silently corrupts every verdict.

The generator verified 4,170 shipped test cases against ITS implementation of these
rules. If ours drifts, those verifications mean nothing and students get Accepted on
wrong answers — or WA on right ones, which is worse for trust.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from judge.compare import compare  # noqa: E402


class Exact(unittest.TestCase):
    def test_basics(self):
        self.assertTrue(compare(3, 3))
        self.assertTrue(compare([1, 2], [1, 2]))
        self.assertTrue(compare({"a": 1}, {"a": 1}))
        self.assertFalse(compare([1, 2], [2, 1]))

    def test_order_matters(self):
        self.assertFalse(compare([["ab", "ba"], ["x"]], [["x"], ["ab", "ba"]]))


class Unordered(unittest.TestCase):
    def test_top_level(self):
        self.assertTrue(compare([3, 1, 2], [1, 2, 3], "unordered"))

    def test_nested_groups(self):
        # The anagram-grouping shape: groups in any order, members in any order.
        got = [["ba", "ab"], ["x"]]
        exp = [["x"], ["ab", "ba"]]
        self.assertTrue(compare(got, exp, "unordered"))

    def test_still_detects_wrong_content(self):
        self.assertFalse(compare([["ab"], ["x"]], [["ab", "ba"], ["x"]], "unordered"))

    def test_mixed_types_do_not_raise(self):
        # sorted() on mixed types raises in Python 3; real test data mixes them.
        self.assertTrue(compare([1, "a", [2]], [[2], "a", 1], "unordered"))


class Floats(unittest.TestCase):
    def test_tolerance(self):
        self.assertTrue(compare(0.1 + 0.2, 0.3, "float"))
        self.assertFalse(compare(0.31, 0.3, "float"))

    def test_near_zero_guard(self):
        # Plain relative tolerance demands exactness at 0.0; the max(1,|b|) guard is
        # what makes "expected 0.0" survive normal float error.
        self.assertTrue(compare(1e-9, 0.0, "float"))
        self.assertFalse(compare(0.5, 0.0, "float"))

    def test_recurses_into_structures(self):
        self.assertTrue(compare([1.0000000001, {"x": 2.0}], [1.0, {"x": 2.0}], "float"))
        self.assertFalse(compare([1.5], [1.0], "float"))

    def test_shape_mismatch_is_not_close(self):
        self.assertFalse(compare([1.0], [1.0, 2.0], "float"))
        self.assertFalse(compare({"a": 1.0}, {"b": 1.0}, "float"))


class BoolsAreNotNumbers(unittest.TestCase):
    def test_true_is_not_one(self):
        # In Python True == 1. A problem returning a count must not be satisfied by
        # returning True, so float mode compares bools identically.
        self.assertFalse(compare(True, 1, "float"))
        self.assertTrue(compare(True, True, "float"))


if __name__ == "__main__":
    unittest.main()
