"""Signed GET against Cloudflare R2 — standard library only.

The judge needs to READ the hidden dataset variants, which live in a private bucket
because they are the answer key. That is the whole reason this file exists, and the
whole reason it is read-only: the credentials it uses are scoped to one bucket with
Object Read permission, so a compromise of this repo cannot alter what students are
judged against.

Implements AWS SigV4 rather than pulling in boto3. requirements.txt is deliberately
empty — a dependency here is a dependency the sandbox image may end up carrying, and
container startup is the dominant cost of every batch.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import os
import urllib.error
import urllib.parse
import urllib.request

ACCOUNT = os.environ.get("R2_ACCOUNT_ID", "")
BUCKET = os.environ.get("R2_BUCKET_DATASETS", "pda-datasets")
KEY_ID = os.environ.get("R2_DATASETS_ACCESS_KEY_ID", "")
SECRET = os.environ.get("R2_DATASETS_SECRET_ACCESS_KEY", "")

_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def configured() -> bool:
    return bool(ACCOUNT and KEY_ID and SECRET)


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def get_object(key: str, timeout: int = 60) -> bytes:
    """Downloads one object. Raises RuntimeError with a status, never a response body."""
    if not configured():
        raise RuntimeError(
            "R2 credentials are not set — a dataset-backed question cannot be judged. "
            "Set R2_ACCOUNT_ID / R2_DATASETS_ACCESS_KEY_ID / R2_DATASETS_SECRET_ACCESS_KEY."
        )

    host = f"{ACCOUNT}.r2.cloudflarestorage.com"
    now = _dt.datetime.now(_dt.timezone.utc)
    amz = now.strftime("%Y%m%dT%H%M%SZ")
    day = amz[:8]
    scope = f"{day}/auto/s3/aws4_request"
    # quote each segment: a key containing '/' must keep its slashes unescaped.
    uri = "/" + "/".join(urllib.parse.quote(p, safe="") for p in f"{BUCKET}/{key}".split("/"))

    canonical = "\n".join([
        "GET", uri, "",
        f"host:{host}\nx-amz-content-sha256:{_EMPTY_SHA}\nx-amz-date:{amz}\n",
        "host;x-amz-content-sha256;x-amz-date",
        _EMPTY_SHA,
    ])
    to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz, scope,
        hashlib.sha256(canonical.encode()).hexdigest(),
    ])
    k = _sign(f"AWS4{SECRET}".encode(), day)
    for part in ("auto", "s3", "aws4_request"):
        k = _sign(k, part)
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        f"https://{host}{uri}",
        headers={
            "Host": host,
            "x-amz-content-sha256": _EMPTY_SHA,
            "x-amz-date": amz,
            "Authorization": (
                f"AWS4-HMAC-SHA256 Credential={KEY_ID}/{scope}, "
                "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
                f"Signature={sig}"
            ),
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return res.read()
    except urllib.error.HTTPError as e:
        # Status only. An S3 error document echoes request metadata, and this runs with
        # log output that anyone can read on a public repo.
        raise RuntimeError(f"R2 GET {BUCKET}/{key} failed: HTTP {e.code}") from None


def get_public(url: str, timeout: int = 60) -> bytes:
    """Fetches the public variant from the CDN — no credentials, and cache-friendly."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as res:
            return res.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GET {url} failed: HTTP {e.code}") from None
