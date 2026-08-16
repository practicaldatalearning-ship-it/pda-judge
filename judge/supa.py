"""Supabase RPC client — service_role. The only place a secret is used."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def rpc(name: str, params: dict[str, Any] | None = None, timeout: int = 60) -> Any:
    if not URL or not KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set")
    req = urllib.request.Request(
        f"{URL}/rest/v1/rpc/{name}",
        data=json.dumps(params or {}).encode(),
        headers={
            "Content-Type": "application/json",
            "apikey": KEY,
            "Authorization": f"Bearer {KEY}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode() or "null")
    except urllib.error.HTTPError as e:
        # Never echo the body verbatim into logs — an error page can contain the key.
        raise RuntimeError(f"{name} failed: HTTP {e.code}") from None
