"""Resolves the dataset variants a question is judged against, and caches them on disk.

A dataset-backed SQL question does not carry its own schema. Each of its test cases names
a VARIANT of a shared database — `v0` is the public snapshot the student explored, and
`v1`…`v14` are private snapshots that each carry one deliberate anomaly (unpaid orders, an
empty table, a management cycle). The student's query is run against all of them, which is
how a careless answer that passes the data they can see still fails.

Two sources, deliberately:
  v0        the public CDN URL — no credentials, edge-cached, and byte-identical to what
            the browser ran, so Run and Submit cannot disagree about the data.
  the rest  the private bucket, with read-only credentials.

Files are cached for the life of the process. A batch is usually several students on the
same question, and re-downloading 15 databases per submission would dominate the runtime.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

from judge import r2

_CACHE_DIR: str | None = None
# (dataset_id, variant_id) -> local path
_FILES: dict[tuple[str, str], str] = {}


def _cache_dir() -> str:
    global _CACHE_DIR
    if _CACHE_DIR is None:
        _CACHE_DIR = tempfile.mkdtemp(prefix="pda-datasets-")
    return _CACHE_DIR


def variants_needed(tests: list[dict[str, Any]]) -> list[str]:
    """The variant ids referenced by these tests, in first-seen order.

    For a dataset-backed question `input[0]` is a variant id. Only what the tests
    actually reference is downloaded — a question judged on 3 variants should not pull
    all 15.
    """
    seen: list[str] = []
    for t in tests:
        inp = t.get("input") or []
        if inp and isinstance(inp[0], str) and inp[0] not in seen:
            seen.append(inp[0])
    return seen


def fetch(dataset: dict[str, Any], variant_ids: list[str]) -> dict[str, str]:
    """Downloads the named variants and returns {variant_id: local path}.

    `dataset` is the payload of mobile_svc_practice_dataset: id, publicUrl, variants[].
    Raises RuntimeError if a referenced variant is not registered — judging a submission
    against a dataset we could not fully assemble would produce a wrong verdict, which is
    worse than reporting the failure.
    """
    ds_id = str(dataset.get("id") or "")
    public_url = str(dataset.get("publicUrl") or "")
    by_id = {str(v.get("id")): v for v in (dataset.get("variants") or [])}

    out: dict[str, str] = {}
    for vid in variant_ids:
        cache_key = (ds_id, vid)
        if cache_key in _FILES:
            out[vid] = _FILES[cache_key]
            continue

        meta = by_id.get(vid)
        if not meta:
            raise RuntimeError(f"dataset '{ds_id}' has no variant '{vid}'")

        # v0 is the one variant that is public. Fetching it over the CDN keeps the
        # private credentials scoped to what is genuinely secret, and it is the exact
        # file the student's browser loaded.
        is_public = vid == str(dataset.get("publicVariant") or "v0")
        if is_public and public_url:
            blob = r2.get_public(public_url)
        else:
            key = str(meta.get("key") or f"datasets/{ds_id}/{vid}.db")
            blob = r2.get_object(key)

        if not blob.startswith(b"SQLite format 3\x00"):
            # A truncated download or an error page saved as a .db would surface later as
            # "file is not a database" attributed to the student's query.
            raise RuntimeError(f"{ds_id}/{vid} is not a SQLite file ({len(blob)} bytes)")

        path = os.path.join(_cache_dir(), f"{ds_id}.{vid}.db")
        with open(path, "wb") as fh:
            fh.write(blob)
        os.chmod(path, 0o444)
        _FILES[cache_key] = path
        out[vid] = path

    return out
