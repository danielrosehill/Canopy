"""The seed service: ask the API to apply the config, report what happened, then exit.

Runs on every `docker compose up`, and is safe to repeat. It holds no gateway credentials:
the work is in `canopy.control`, behind the API.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import httpx

API_URL = os.environ.get("CANOPY_API_URL", "http://api:8090").rstrip("/")
TOKEN_FILE = Path(os.environ.get("CANOPY_API_DIR", "/api")) / "token"
APPLY_TIMEOUT = float(os.environ.get("SEED_TIMEOUT_SEC", "900"))


def wait_for_api(seconds: int = 120) -> None:
    deadline = time.time() + seconds
    while True:
        try:
            if httpx.get(f"{API_URL}/health", timeout=5).is_success:
                return
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            sys.exit(f"the API did not answer at {API_URL} within {seconds}s")
        time.sleep(2)


def report(reply: dict) -> int:
    for line in reply.get("log", []):
        print(f"  {line}", flush=True)
    failures = reply.get("failures", [])
    for line in failures:
        print(f"  FAILED {line}", flush=True)
    print(f"seed finished with {len(failures)} problem(s)", flush=True)
    return 1 if failures else 0


def main() -> None:
    wait_for_api()
    token = os.environ.get("CANOPY_API_TOKEN") or TOKEN_FILE.read_text().strip()
    r = httpx.post(f"{API_URL}/v1/config/apply", headers={"Authorization": f"Bearer {token}"},
                   timeout=APPLY_TIMEOUT)
    if not r.is_success and "failures" not in r.text:
        sys.exit(f"apply failed: {r.status_code} {r.text[:500]}")
    sys.exit(report(r.json()))


if __name__ == "__main__":
    main()
