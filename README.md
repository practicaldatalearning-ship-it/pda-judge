# pda-judge

Server-side judge for PDA practice submissions.

**Why this exists:** grading used to happen in the student's browser, and the verdict was
sent to the server to be trusted. Anyone could post `verdict='AC'` with an empty code
string and collect points — and separately, every hidden test with its expected value was
readable by any logged-in student. Both are closed by judging here instead.

**Why it is a separate repo from `pda-grader`:**

- `pda-grader` must stay **private** (it holds assignment content and `COACH_KEY`).
  This repo can be **public**, and public repos get *unlimited* free Actions minutes
  where private repos get 2,000/month. That is what makes judging free.
- `pda-grader` uses `concurrency: group: grade`. Sharing it would put every student's
  verdict behind a long assignment batch.
- Least privilege: this repo's only secret is a Supabase service key.
- A far smaller sandbox image (no pandas/sklearn/nbclient) cuts container startup from
  tens of seconds to about one — and startup is paid on every batch.

**It is safe for this repo to be public.** It contains only the harness. Questions, tests
and expected values are fetched at runtime with a secret that lives in GitHub, never in
the tree. Fork pull requests receive no secrets, and `repository_dispatch` runs only from
the default branch.

---

## How a submission is judged

```
student hits Submit (pda-public)
  → mobile_le_practice_submit    inserts a row with status='queued'
  → repository_dispatch          wakes this workflow (~30 s path)
  → mobile_svc_practice_judge_take    round-robin claim, marks 'judging'
  → mobile_svc_practice_judge_tests   the answer key, service_role only
  → docker run --network none         the student's code, per-test timer
  → mobile_svc_practice_judge_post    verdict + points; the ONLY place either can
                                      originate
```

**The queue row is the source of truth; this workflow is disposable.** A dropped
dispatch, a dead runner or a GitHub outage delays a verdict — it cannot lose one. The
5-minute cron finds anything the dispatch missed, and rows stuck in `judging` for ten
minutes are automatically returned to `queued`.

## Layout

```
judge/compare.py   comparison semantics — exact | unordered | float
judge/harness.py   runs INSIDE the container; the only thing that touches student code
judge/runner.py    builds the container; where every restriction is actually set
judge/supa.py      service-role RPC client — the only place the secret is used
judge/main.py      one batch: claim → judge → post
sandbox/Dockerfile bare python:3.11-slim, non-root, no shell
tests/             the comparison rules (see the warning below)
```

### `tests/test_compare.py` is the most important file here

If `compare` drifts from the generator's rules, students are told Accepted on wrong
answers — or WA on right ones, which damages trust faster. The generator verified 4,170
shipped test cases against *its* implementation of these rules; ours has to agree.
CI runs these before any judging happens, and a failure stops the batch.

## Deploy

1. Create the repo on GitHub as **public**, named `pda-judge`.
2. Settings → Secrets and variables → Actions → **repository** secrets:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
3. Push. The first scheduled run will build the sandbox and drain an empty queue.
4. Wire the fast path — from the submit RPC via `pg_net`, or from a Worker:
   ```
   POST https://api.github.com/repos/<owner>/pda-judge/dispatches
   Authorization: Bearer <PAT with `repo` scope>
   {"event_type": "judge-batch"}
   ```
   Skipping this still works; verdicts just arrive on the 5-minute cron instead of in
   ~30 s.

**Never add a self-hosted runner to this repo while it is public** — any pull request
would then run arbitrary code on that machine. If self-hosting is ever needed, make the
repo private first and accept the 2,000-minute cap.

## Config

Tunable from pda-admin `/config` (`mobile.app_config`), not from here:

| key | default | meaning |
|---|---|---|
| `practice.judge_batch` | 20 | submissions claimed per run |
| `practice.judge_stale_minutes` | 10 | before a claimed row is reclaimed |
| `practice.max_queued_per_user` | 5 | one student cannot fill the queue |
| `practice.hint_penalty_pct` | 15 | points deducted per hint revealed |

Runner-side env: `JUDGE_BATCH`, `JUDGE_BUDGET_SECONDS` (default 420), `JUDGE_MEMORY`
(512m), `JUDGE_PIDS` (128).

## Known limitations

- **Python only.** SQL questions (`language='sql'`) are claimed but will fail with
  "no callable named `solve`". Add a SQL branch to the harness before publishing a SQL
  sheet.
- **Tests come from Postgres, not R2.** Fine at current volume; at roughly 5,000
  submissions/day the payload would approach Supabase's free egress tier. The fix is to
  write `hidden_tests` to R2 at import time and fetch from there — R2 egress is free —
  and it is localised to `mobile_svc_practice_judge_tests` plus the importer.
- **Free-tier concurrency is ~20 jobs.** With batching that is roughly 3,000 submissions
  per cycle; beyond that, the escape hatches are Workers Paid ($5/mo, synchronous 1–3 s)
  or a private repo with a self-hosted runner.
