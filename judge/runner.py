"""Executes one submission in a throwaway container and parses its verdict.

The container is the security boundary, so every restriction is set HERE — the harness
inside cannot be trusted to limit itself once student code is running in the same
interpreter.

  --network none     no exfiltration, no calling home, no pulling a solution
  --read-only        plus a small tmpfs; nothing persists between submissions
  --memory / --pids  a fork bomb or a runaway allocation dies instead of taking the runner
  env cleared        THE IMPORTANT ONE: the job holds a Supabase service key, and
                     student code must never see it
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import Any

IMAGE = os.environ.get("JUDGE_IMAGE", "pda-judge-sandbox")
MEMORY = os.environ.get("JUDGE_MEMORY", "512m")
PIDS = os.environ.get("JUDGE_PIDS", "128")


def run_submission(code: str, tests: list[dict[str, Any]], compare_mode: str,
                   time_limit_ms: int, language: str = "python",
                   setup_sql: str = "") -> dict[str, Any]:
    """Returns the harness verdict dict; never raises for a bad submission."""
    work = {"code": code, "tests": tests, "compareMode": compare_mode,
            "timeLimitMs": time_limit_ms, "language": language, "setupSql": setup_sql}

    with tempfile.TemporaryDirectory() as tmp:
        work_path = os.path.join(tmp, "work.json")
        with open(work_path, "w", encoding="utf-8") as fh:
            json.dump(work, fh)

        # Wall-clock ceiling for the whole container. The harness enforces the per-test
        # limit; this catches the case where the harness itself is wedged, and it scales
        # with test count so a 675-case question is not killed for being large.
        wall = min(600, 60 + (time_limit_ms / 1000) * 2 + len(tests) * 0.05)

        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--memory", MEMORY, "--memory-swap", MEMORY,
            "--pids-limit", PIDS,
            "--cpus", "1",
            "--read-only", "--tmpfs", "/tmp:size=64m",
            "-v", f"{work_path}:/work/work.json:ro",
            IMAGE,
            "python", "/app/harness.py", "/work/work.json",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=wall)
        except subprocess.TimeoutExpired:
            return {"verdict": "TLE", "passed": 0, "total": len(tests),
                    "runtimeMs": int(wall * 1000), "failedCase": None,
                    "error": "container exceeded its wall-clock limit"}

        # stdout is a single JSON line. Anything else means the container died in a way
        # the harness could not report — surface it rather than guessing a verdict.
        line = (proc.stdout or "").strip().splitlines()
        if not line:
            return {"verdict": "RE", "passed": 0, "total": len(tests), "runtimeMs": 0,
                    "failedCase": None,
                    "error": (proc.stderr or "no output from the sandbox")[-400:]}
        try:
            return json.loads(line[-1])
        except json.JSONDecodeError:
            return {"verdict": "RE", "passed": 0, "total": len(tests), "runtimeMs": 0,
                    "failedCase": None, "error": f"unparseable sandbox output: {line[-1][:200]}"}
