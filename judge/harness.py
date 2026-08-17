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
from compare import compare, compare_sql  # noqa: E402  (same dir inside the container)


class Timeout(Exception):
    pass


# The sandbox is Linux, where SIGALRM always exists — this is the per-test time limit and
# it is not optional there. The guard exists so the suite can be RUN on a developer's
# Windows machine; the container's own wall-clock ceiling in runner.py is the backstop
# either way, so nothing in production depends on this being a no-op.
_HAS_ALARM = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")


def _arm(seconds: float) -> None:
    if not _HAS_ALARM:
        return
    signal.signal(signal.SIGALRM, _alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)


def _disarm() -> None:
    if _HAS_ALARM:
        signal.setitimer(signal.ITIMER_REAL, 0)


def _alarm(_sig, _frm):
    raise Timeout()


def _deny_attach(action: int, *_rest: Any) -> int:
    """SQLite authorizer: block ATTACH/DETACH, allow everything else.

    The dataset variants are mounted in one directory, so without this a submission could
    `ATTACH 'v7.db'` and query a snapshot it was not being judged on. It is not much of a
    leak on its own — the student never sees query output, only a verdict — but a question
    is supposed to be answered against the database it names, and nothing legitimate needs
    ATTACH here.
    """
    import sqlite3
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_sql(work: dict[str, Any]) -> dict[str, Any]:
    """Judges a SQL submission against SQLite.

    SQLite specifically, and not by accident: pda-public's Code Lab runs sql.js in the
    browser, which IS SQLite. Judging on Postgres would mean a student's query passing in
    the lab and failing here on a dialect difference they cannot see — the judge has to
    speak the same SQL the student was taught in.

    Two shapes, chosen per test:

    * **dataset-backed** — `input[0]` is a variant id and `work["datasets"]` maps it to a
      read-only .db file mounted into the container. The file is the same one the browser
      downloaded for the public variant, so Run and Submit cannot disagree about the data.
    * **inline** (legacy) — schema from `setupSql`, rows from the test's own seed SQL,
      into a fresh `:memory:` database.

    Either way each test gets its own connection. A query with side effects, or a previous
    test's rows, must not colour the next case.
    """
    import sqlite3

    sql: str = work["code"]
    setup: str = work.get("setupSql") or ""
    datasets: dict[str, str] = work.get("datasets") or {}
    tests: list[dict[str, Any]] = work["tests"]
    mode: str = work.get("compareMode", "exact")
    limit_ms: int = int(work.get("timeLimitMs", 5000))

    out: dict[str, Any] = {"verdict": "RE", "passed": 0, "total": len(tests),
                           "runtimeMs": 0, "failedCase": None, "error": ""}
    started = time.perf_counter()
    passed = 0

    for i, t in enumerate(tests):
        con = None
        _arm(limit_ms / 1000)
        try:
            seed = t.get("input") or []
            variant = seed[0] if seed and isinstance(seed[0], str) else None

            if datasets:
                path = datasets.get(variant or "")
                if not path:
                    # Not the student's fault, so it must not read as their error.
                    out.update(verdict="RE", passed=passed, failedCase=i,
                               error=f"dataset variant '{variant}' was not provided to the sandbox")
                    break
                # immutable=1: the file is mounted read-only, so SQLite must not try to
                # create a journal or -shm beside it. It also skips locking entirely,
                # which matters when the same file backs every test in the batch.
                con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
                con.set_authorizer(_deny_attach)
            else:
                con = sqlite3.connect(":memory:")
                if setup.strip():
                    con.executescript(setup)
                if variant:
                    con.executescript(variant)

            cur = con.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = [list(r) for r in cur.fetchall()]
            got = {"columns": cols, "rows": rows}
        except Timeout:
            out.update(verdict="TLE", passed=passed, failedCase=i,
                       error=f"exceeded {limit_ms} ms on test {i + 1}")
            break
        except sqlite3.Error as e:
            # A SQL error is the equivalent of a compile error: the query never ran.
            out.update(verdict="CE" if i == 0 else "RE", passed=passed, failedCase=i,
                       error=f"SQL error: {e}"[:400])
            break
        except BaseException as e:  # noqa: BLE001
            out.update(verdict="RE", passed=passed, failedCase=i,
                       error=f"{type(e).__name__}: {e}"[:400])
            break
        finally:
            _disarm()
            if con is not None:
                con.close()

        if compare_sql(got, t["expected"], mode):
            passed += 1
        else:
            out.update(verdict="WA", passed=passed, failedCase=i, error="")
            break
    else:
        out["verdict"] = "AC"
        out["passed"] = passed

    out["runtimeMs"] = int((time.perf_counter() - started) * 1000)
    return out


def main() -> int:
    with open(sys.argv[1], "r", encoding="utf-8") as fh:
        work = json.load(fh)

    if (work.get("language") or "python") == "sql":
        print(json.dumps(run_sql(work)))
        return 0

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
    _arm(limit_ms / 1000)
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
        _disarm()

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
        _arm(limit_ms / 1000)
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
            _disarm()

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
