"""Comparison semantics for practice verdicts.

These MUST match the generator's local runner (SOP §5). Its 4,170 shipped test cases
were verified against those rules, and if this file disagrees even slightly, every one
of those verifications is meaningless and students get Accepted on wrong answers (or,
worse, WA on right ones).

Three modes:
  exact      got == expected
  unordered  recursively sorted, for statements that say "any order"
  float      relative tolerance 1e-6, applied to the WHOLE returned structure
"""

from __future__ import annotations

import json
from typing import Any

REL_TOL = 1e-6


def _canon(v: Any) -> str:
    """Stable key for sorting heterogeneous values.

    Sorting a list of mixed types raises in Python 3, and test data legitimately mixes
    ints, strings and lists, so sort by a canonical serialisation instead of by value.
    """
    return json.dumps(v, sort_keys=True, default=str)


def _sorted_deep(v: Any) -> Any:
    if isinstance(v, list):
        return sorted((_sorted_deep(x) for x in v), key=_canon)
    if isinstance(v, dict):
        return {k: _sorted_deep(v[k]) for k in sorted(v)}
    return v


def _close(a: Any, b: Any) -> bool:
    """Relative tolerance with a near-zero guard.

    Plain relative tolerance collapses when the expected value is 0.0 — `abs(a-b) <=
    tol*abs(b)` demands exactness there. max(1, |b|) keeps it meaningful at zero without
    being sloppy at scale.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= REL_TOL * max(1.0, abs(b))
    return False


def _eq_float(a: Any, b: Any) -> bool:
    if isinstance(b, list):
        if not isinstance(a, list) or len(a) != len(b):
            return False
        return all(_eq_float(x, y) for x, y in zip(a, b))
    if isinstance(b, dict):
        if not isinstance(a, dict) or set(a) != set(b):
            return False
        return all(_eq_float(a[k], b[k]) for k in b)
    if isinstance(b, (int, float)) and not isinstance(b, bool):
        return _close(a, b)
    return a == b


def compare(got: Any, expected: Any, mode: str = "exact") -> bool:
    if mode == "unordered":
        return _sorted_deep(got) == _sorted_deep(expected)
    if mode == "float":
        return _eq_float(got, expected)
    return got == expected
