---
name: stage-file
description: Put a local file in Canopy's staging store and get a presigned URL. Use when a tool needs a URL for a file, on "stage this file", or "give me a link to this file".
---

# Stage a file

Needs the `storage` profile. Add `storage` to `COMPOSE_PROFILES`, run `canopy up`, and the first stage sets
Garage up, so there is nothing else to configure.

```bash
canopy stage report.pdf                        # a URL for anything outside the stack, valid for 1 hour
canopy stage report.pdf --audience stack       # a URL for a service on the stack network
canopy stage report.pdf --expires 7200         # in seconds
canopy buckets                                 # buckets, objects and bytes
```

The reply is JSON: use `url`. **A URL works only for the hostname it was signed for,** so pick the audience by
who fetches the file. A tool running on the server is `stack`. For `remote`, the host is
`CANOPY_STORAGE_PUBLIC_URL`, which must be an address the fetcher can reach, and `CANOPY_STORAGE_BIND` must
expose the port. Check that first if a remote fetch fails: a wrong host fails with 403, an unreachable one with
a timeout.

The `transient` bucket deletes objects after a day, so a URL cannot outlive that, and a longer `--expires` is
refused. Files over `CANOPY_STAGE_MAX_MB` (256) are refused too. Staged files are not backed up.

More buckets go in `agent-data/config/storage.json`, then restart `api`. A bucket with no `expire_days` keeps
what it holds:

```json
{"buckets": [{"name": "transient", "expire_days": 1}, {"name": "shared", "expire_days": 7}]}
```

The `storage-expiry` job checks daily that the rules are in place and nothing is overdue. If it fails, read
`canopy jobs runs storage-expiry`.
