"""One judging batch: claim → judge → post.

Runs on a GitHub Actions runner. Everything about reliability lives in the DATABASE
(the queue row, the round-robin claim, the stale reclaim) — this process is disposable
and may die at any point without losing a submission.

Two rules it holds to:

1. **Post what you have, always.** If the batch is interrupted, already-judged results
   are posted before exiting. Anything unposted stays `judging` and is reclaimed after
   ten minutes, so a crash costs a delay, never a lost verdict.
2. **A broken submission is a verdict, not a crash.** One question with unreadable tests
   must not take the other nineteen down with it.
"""

from __future__ import annotations

import os
import sys
import time

from judge.runner import run_submission
from judge.supa import rpc

BATCH = int(os.environ.get("JUDGE_BATCH", "20"))
# Leave room inside the job's timeout to post results and upload logs. Exceeding this
# stops CLAIMING more work; it never abandons work already claimed.
BUDGET_S = int(os.environ.get("JUDGE_BUDGET_SECONDS", "420"))


def main() -> int:
    started = time.time()
    batch = rpc("mobile_svc_practice_judge_take", {"p_limit": BATCH}) or []
    if not batch:
        print("queue empty — nothing to judge")
        return 0

    print(f"claimed {len(batch)} submission(s)")
    tests_cache: dict[str, dict] = {}
    results: list[dict] = []

    def flush() -> None:
        if not results:
            return
        posted = rpc("mobile_svc_practice_judge_post", {"p_results": results})
        print(f"posted {posted}")
        results.clear()

    try:
        for i, sub in enumerate(batch, 1):
            sid = sub["submissionId"]
            qid = sub["questionId"]
            try:
                if qid not in tests_cache:
                    # Cached per question: a batch is usually several students on the
                    # same problem, and the payload is the largest thing we move.
                    tests_cache[qid] = rpc("mobile_svc_practice_judge_tests",
                                           {"p_question_id": qid}) or {}
                spec = tests_cache[qid]
                if spec.get("error") or not spec.get("hiddenTests"):
                    results.append({"submissionId": sid, "verdict": "RE", "passed": 0,
                                    "total": 0, "error": "no tests for this question"})
                    continue

                # Visible first: a failure there is the clearest possible feedback,
                # and it matches the order the student saw when they hit Run.
                tests = list(spec.get("visibleTests") or []) + list(spec["hiddenTests"])
                out = run_submission(
                    code=sub["code"],
                    tests=tests,
                    compare_mode=spec.get("compareMode", sub.get("compareMode", "exact")),
                    time_limit_ms=int(spec.get("timeLimitMs", sub.get("timeLimitMs", 5000))),
                )
                out["submissionId"] = sid
                out["total"] = len(tests)
                results.append(out)
                print(f"  [{i}/{len(batch)}] {sid[:8]} → {out['verdict']} "
                      f"{out.get('passed')}/{len(tests)} in {out.get('runtimeMs')}ms")

            except Exception as e:  # noqa: BLE001 — one bad row must not sink the batch
                print(f"  [{i}/{len(batch)}] {sid[:8]} → judge error: {e}", file=sys.stderr)
                results.append({"submissionId": sid, "verdict": "RE", "passed": 0,
                                "total": 0, "error": f"judge error: {e}"[:400]})

            # Post as we go: a runner killed mid-batch still delivers what it finished.
            if len(results) >= 5:
                flush()

            if time.time() - started > BUDGET_S:
                print(f"time budget reached after {i}/{len(batch)}; "
                      f"the rest stay claimed and are reclaimed in 10 min")
                break
    finally:
        flush()

    return 0


if __name__ == "__main__":
    sys.exit(main())
