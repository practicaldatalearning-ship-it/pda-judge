"""Runs INSIDE the sandbox container. Never imports anything from the judge package.

Reads one work file, executes the student's `solve` against each test, prints a single
JSON verdict line to stdout. Nothing else is printed — the runner parses stdout.

Isolation this file relies on (set by the caller, not here):
  docker run --network none --memory ... --pids-limit ...   and NO secrets in env.

Student code is arbitrary and hostile by assumption. This file therefore never trusts it
to terminate, to leave its arguments alone, or to stay quiet.
"""

from __future__ import annotations

import copy
import json
import os
import signal
import sys
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compare import compare  # noqa: E402  (same dir inside the container)


class Timeout(Exception):
    pass


def _alarm(_sig, _frm):
    raise Timeout()


def main() -> int:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        work = json.load(fh)

    code: str = work["code"]
    tests: list[dict[str, Any]] = work["tests"]
    mode: str = work.get("compareMode", "exact")
    limit_ms: int = int(work.get("timeLimitMs", 5000))

    out: dict[str, Any] = {"verdict": "RE", "passed": 0, "total": len(tests),
                           "runtimeMs": 0, "failedCase": None, "error": ""}

    # ---- compile + import the student's module -------------------------------
    ns: dict[str, Any] = {}
    try:
        compiled = compile(code, "<submission>", "exec")
    except SyntaxError as e:
        out["verdict"] = "CE"
        out["error"] = f"SyntaxError: {e.msg} (line {e.lineno})"
        print(json.dumps(out))
        return 0

    # Module-level code runs under the time limit too — `while True` at import
    # would otherwise hang before a single test ran.
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, limit_ms / 1000)
    try:
        exec(compiled, ns)  # noqa: S102 — that is the entire job
    except Timeout:
        out["verdict"] = "TLE"
        out["error"] = "timed out while loading the submission"
        print(json.dumps(out))
        return 0
    except BaseException as e:  # noqa: BLE001 — a student may raise anything
        out["verdict"] = "RE"
        out["error"] = f"{type(e).__name__}: {e}"[:400]
        print(json.dumps(out))
        return 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)

    solve = ns.get("solve")
    if not callable(solve):
        out["verdict"] = "CE"
        out["error"] = "no callable named `solve` was defined"
        print(json.dumps(out))
        return 0

    # ---- run the tests -------------------------------------------------------
    started = time.perf_counter()
    passed = 0
    for i, t in enumerate(tests):
        # Deep-copy per test: a solution that mutates its argument must not corrupt
        # the next case. This is a real failure mode, not a theoretical one.
        args = copy.deepcopy(t["input"])
        signal.setitimer(signal.ITIMER_REAL, limit_ms / 1000)
        try:
            got = solve(*args)
        except Timeout:
            out.update(verdict="TLE", passed=passed, failedCase=i,
                       error=f"exceeded {limit_ms} ms on test {i + 1}")
            break
        except BaseException as e:  # noqa: BLE001
            out.update(verdict="RE", passed=passed, failedCase=i,
                       error=f"{type(e).__name__}: {e}"[:400])
            break
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)

        if compare(got, t["expected"], mode):
            passed += 1
        else:
            out.update(verdict="WA", passed=passed, failedCase=i, error="")
            break
    else:
        out["verdict"] = "AC"
        out["passed"] = passed

    out["runtimeMs"] = int((time.perf_counter() - started) * 1000)
    # The ONLY line on stdout. Student prints go to stderr via the runner's redirect.
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
