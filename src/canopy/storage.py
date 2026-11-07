"""Staging on Garage: the `storage` profile. See docs/design.md.

The image has no shell, so this code does the bootstrap through Garage's admin API. `prepare` writes
the two secrets Garage reads at start, which is why `storage` starts after `api`. `ensure` does the
rest on first use: layout, buckets, one key, and the expiry rules. Every step is safe to repeat.

`agent-data/config/storage.json` is optional, and names the buckets. The first is the default:

    {"buckets": [{"name": "transient", "expire_days": 1}]}

A bucket with `expire_days` is transient. Without it nothing is deleted. A presigned URL works only
for the hostname it was signed for, so `stage` signs for its audience: `stack` for a service on the
stack network, `remote` for anything else, through CANOPY_STORAGE_PUBLIC_URL.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .control import CONFIG, ControlError, check_name, write_secret

SECRETS = Path(os.environ.get("CANOPY_STORAGE_SECRETS", "/storage-secrets"))
ADMIN_URL = os.environ.get("CANOPY_STORAGE_ADMIN_URL", "http://storage:3903").rstrip("/")
S3_URL = os.environ.get("CANOPY_STORAGE_URL", "http://storage:3900").rstrip("/")
PUBLIC_URL = os.environ.get("CANOPY_STORAGE_PUBLIC_URL", "http://127.0.0.1:3900").rstrip("/")
CAPACITY = int(os.environ.get("CANOPY_STORAGE_CAPACITY_GB", "10")) * 10**9
MAX_BYTES = int(os.environ.get("CANOPY_STAGE_MAX_MB", "256")) * 2**20
REGION = "us-east-1"   # Must match s3_region in config/garage.toml
KEY_NAME = "canopy-api"
MAX_EXPIRES = 7 * 86400   # the longest SigV4 allows
DEFAULT_EXPIRES = 3600
# Garage's expiry worker runs once a day, so an object can outlive its rule by a day and a bit.
EXPIRY_SLACK_DAYS = 2
DEFAULT_BUCKETS = [{"name": "transient", "expire_days": 1}]
_S3 = "{http://s3.amazonaws.com/doc/2006-03-01/}"


def lifecycle_xml(days: int) -> str:
    return ("<LifecycleConfiguration><Rule><ID>expire</ID><Status>Enabled</Status>"
            f"<Filter><Prefix></Prefix></Filter><Expiration><Days>{days}</Days></Expiration>"
            "</Rule></LifecycleConfiguration>")


def lifecycle_days(xml: str) -> int | None:
    """The days of the first enabled expiry rule, however the store namespaces its reply."""
    for rule in ET.fromstring(xml).iter():
        if rule.tag.rsplit("}", 1)[-1] != "Rule":
            continue
        fields = {e.tag.rsplit("}", 1)[-1]: e.text for e in rule.iter()}
        if fields.get("Status") == "Enabled" and fields.get("Days"):
            return int(fields["Days"])
    return None


def safe_name(name: str) -> str:
    """The last part of a path, in characters that survive every client's URL handling."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", name.replace("\\", "/").rsplit("/", 1)[-1]).strip("._")
    return base[:120] or "file"


def presign(method: str, base: str, path: str, key: str, secret: str, expires: int,
            query: dict[str, str] | None = None, now: datetime | None = None) -> str:
    """SigV4 query signing, with `host` the only signed header and the payload unsigned."""
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    scope = f"{stamp[:8]}/{REGION}/s3/aws4_request"
    params = {**(query or {}), "X-Amz-Algorithm": "AWS4-HMAC-SHA256", "X-Amz-Credential": f"{key}/{scope}",
              "X-Amz-Date": stamp, "X-Amz-Expires": str(expires), "X-Amz-SignedHeaders": "host"}
    encoded = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(params.items()))
    path = quote(path, safe="/-_.~")
    canonical = "\n".join([method, path, encoded, f"host:{urlsplit(base).netloc}\n", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    signing = f"AWS4{secret}".encode()
    for part in (stamp[:8], REGION, "s3", "aws4_request"):
        signing = hmac.new(signing, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{base}{path}?{encoded}&X-Amz-Signature={signature}"


class Storage:
    def __init__(self, secrets_dir: Path = SECRETS, config: Path = CONFIG, admin_url: str = ADMIN_URL,
                 s3_url: str = S3_URL, public_url: str = PUBLIC_URL):
        self.dir, self.config = secrets_dir, config
        self.admin_url, self.s3_url, self.public_url = admin_url, s3_url, public_url
        self.http = httpx.Client(timeout=60)
        self.lock = threading.Lock()
        self.ready: str | None = None   # the bucket config `ensure` last set up

    def prepare(self) -> None:
        """Before `storage` starts: it reads both of these, and has no way to make them."""
        for name, make in (("rpc_secret", lambda: secrets.token_hex(32)),
                           ("admin_token", lambda: secrets.token_urlsafe(32))):
            if not (self.dir / name).exists():
                write_secret(self.dir / name, make())

    def buckets_config(self) -> list[dict]:
        path = self.config / "storage.json"
        buckets = json.loads(path.read_text())["buckets"] if path.is_file() else DEFAULT_BUCKETS
        for b in buckets:
            check_name(b["name"])
        return buckets

    def _credentials(self) -> dict | None:
        path = self.dir / "s3.json"
        return json.loads(path.read_text()) if path.is_file() else None

    def _admin(self, method: str, route: str, body: dict | None = None, params: dict | None = None,
               missing_ok: bool = False) -> dict | None:
        token = (self.dir / "admin_token").read_text().strip()
        try:
            r = self.http.request(method, f"{self.admin_url}/v2/{route}", json=body, params=params,
                                  headers={"Authorization": f"Bearer {token}"})
        except httpx.TransportError as e:
            raise ControlError(503, f"storage is not answering, is the storage profile on? {e}") from None
        if missing_ok and r.status_code in (400, 404):
            return None
        if r.status_code >= 300:
            raise ControlError(502, f"storage {route}: {r.status_code} {r.text[:200]}")
        return r.json()

    def _s3(self, method: str, path: str, query: dict[str, str] | None = None, missing_ok: bool = False,
            **kw) -> httpx.Response:
        c = self._credentials()
        url = presign(method, self.s3_url, path, c["key"], c["secret"], 300, query)
        try:
            r = self.http.request(method, url, **kw)
        except httpx.TransportError as e:
            raise ControlError(503, f"storage is not answering: {e}") from None
        if r.status_code == 404 and missing_ok:
            return r
        if r.status_code >= 300:
            if r.status_code == 404:
                self.ready = None   # something ensure made has gone, so make it again next time
            raise ControlError(502, f"storage {method} {path}: {r.status_code} {r.text[:200]}")
        return r

    def _layout(self) -> None:
        status = self._admin("GET", "GetClusterStatus")
        node = status["nodes"][0]
        if node.get("role"):
            return
        self._admin("POST", "UpdateClusterLayout", {"roles": [
            {"id": node["id"], "zone": "canopy", "capacity": CAPACITY, "tags": []}]})
        self._admin("POST", "ApplyClusterLayout", {"version": status["layoutVersion"] + 1})

    def _key(self) -> None:
        c = self._credentials()
        if c and self._admin("GET", "GetKeyInfo", params={"id": c["key"]}, missing_ok=True):
            return
        made = self._admin("POST", "CreateKey", {"name": KEY_NAME})
        write_secret(self.dir / "s3.json", json.dumps({"key": made["accessKeyId"], "secret": made["secretAccessKey"]}))

    def _bucket(self, spec: dict) -> None:
        name, key = spec["name"], self._credentials()["key"]
        info = (self._admin("GET", "GetBucketInfo", params={"globalAlias": name}, missing_ok=True)
                or self._admin("POST", "CreateBucket", {"globalAlias": name}))
        self._admin("POST", "AllowBucketKey", {"bucketId": info["id"], "accessKeyId": key,
                                               "permissions": {"read": True, "write": True, "owner": True}})
        days = spec.get("expire_days")
        if days:
            self._s3("PUT", f"/{name}", {"lifecycle": ""}, content=lifecycle_xml(days))
            if self._rule(name) != days:
                raise ControlError(502, f"storage: the expiry rule on {name} did not read back")

    def _rule(self, bucket: str) -> int | None:
        r = self._s3("GET", f"/{bucket}", {"lifecycle": ""}, missing_ok=True)   # no rule is a 404
        return lifecycle_days(r.text) if r.status_code == 200 else None

    def ensure(self) -> None:
        """Runs again when `storage.json` changes, so a new bucket needs no restart."""
        with self.lock:
            specs = self.buckets_config()
            wanted = json.dumps(specs, sort_keys=True)
            if self.ready != wanted:
                self._layout()
                self._key()
                for spec in specs:
                    self._bucket(spec)
                self.ready = wanted

    def stage(self, data: bytes, name: str, bucket: str | None = None, audience: str = "remote",
              expires: int = DEFAULT_EXPIRES, content_type: str | None = None) -> dict:
        specs = {b["name"]: b for b in self.buckets_config()}
        bucket = bucket or next(iter(specs))
        if bucket not in specs:
            raise ControlError(404, f"no such bucket: {bucket}")
        bases = {"stack": self.s3_url, "remote": self.public_url}
        if audience not in bases:
            raise ControlError(400, f"audience must be one of {sorted(bases)}")
        days = specs[bucket].get("expire_days")
        limit = min(MAX_EXPIRES, days * 86400) if days else MAX_EXPIRES
        if not 1 <= expires <= limit:
            raise ControlError(400, f"expires must be 1 to {limit} seconds on {bucket}")
        self.ensure()
        key = f"{uuid.uuid4().hex[:12]}/{safe_name(name)}"
        path = f"/{bucket}/{key}"
        self._s3("PUT", path, content=data,
                 headers={"Content-Type": content_type} if content_type else None)
        c = self._credentials()
        until = datetime.now(timezone.utc) + timedelta(seconds=expires)
        return {"url": presign("GET", bases[audience], path, c["key"], c["secret"], expires),
                "bucket": bucket, "key": key, "bytes": len(data), "audience": audience,
                "expires_at": until.strftime("%Y-%m-%dT%H:%M:%SZ")}

    def buckets(self) -> list[dict]:
        self.ensure()
        out = []
        for spec in self.buckets_config():
            info = self._admin("GET", "GetBucketInfo", params={"globalAlias": spec["name"]})
            out.append({"name": spec["name"], "expire_days": spec.get("expire_days"),
                        "objects": info["objects"], "bytes": info["bytes"]})
        return out

    def _oldest(self, bucket: str) -> tuple[int, datetime | None]:
        count, oldest, token = 0, None, None
        while True:
            query = {"list-type": "2", **({"continuation-token": token} if token else {})}
            root = ET.fromstring(self._s3("GET", f"/{bucket}", query).text)
            for item in root.iter(f"{_S3}Contents"):
                count += 1
                when = datetime.fromisoformat(item.find(f"{_S3}LastModified").text.replace("Z", "+00:00"))
                oldest = when if oldest is None or when < oldest else oldest
            token = root.findtext(f"{_S3}NextContinuationToken")
            if not token:
                return count, oldest

    def expiry_job(self, now: datetime | None = None) -> str:
        """Garage accepts a rule and reports nothing about running it, so look at what is left."""
        if not self._credentials():
            return "storage has not been used"
        self.ensure()
        now, lines, late = now or datetime.now(timezone.utc), [], []
        for spec in self.buckets_config():
            days = spec.get("expire_days")
            if not days:
                continue
            name = spec["name"]
            if self._rule(name) != days:
                late.append(f"{name}: the expiry rule is missing or changed")
                continue
            count, oldest = self._oldest(name)
            age = (now - oldest).total_seconds() / 86400 if oldest else 0
            lines.append(f"{name}: {count} objects, oldest {age:.1f} days, rule {days}")
            if age > days + EXPIRY_SLACK_DAYS:
                late.append(f"{name}: an object is {age:.1f} days old, past its {days} day rule")
        if late:
            raise ControlError(500, "; ".join(late))
        return "; ".join(lines) or "no transient buckets"

    def actions(self) -> dict:
        return {"storage-expiry": self.expiry_job}

    def check(self) -> dict:
        """For /v1/status. Off is fine, but a store that was in use and went away is not."""
        try:
            self.http.get(f"{self.admin_url}/health", timeout=3)
        except httpx.TransportError as e:
            if self._credentials():
                return {"ok": False, "error": f"storage is not answering: {e}"[:300]}
            return {"ok": True, "state": "off"}
        return {"ok": True, "state": "running" if self._credentials() else "not used yet"}
