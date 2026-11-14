#!/usr/bin/env python3
"""Write docs/api.md from the API's own routes. A route with no entry here fails the run, and
tests/test_api_docs.py fails if the page is out of date.

    uv run scripts/gen-api-docs.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from canopy import api

OUT = Path(__file__).parent.parent / "docs/api.md"

ROUTES = {
    "GET /health": "Liveness, with no token.",
    "GET /v1/status": "Every component: gateway, upstreams, groups, catalogue, toolbox, storage and jobs. `jobs` is red when a job's last run failed, a job is overdue or the scheduler has stopped, and it reports the age of the last backup.",
    "GET /v1/gateway/servers": "Upstreams registered on the gateway.",
    "POST /v1/gateway/servers": "Register an upstream.",
    "DELETE /v1/gateway/servers/{name}": "Remove an upstream.",
    "POST /v1/gateway/servers/{name}/enable": "Put a disabled upstream back in service.",
    "POST /v1/gateway/servers/{name}/disable": "Take an upstream out of service without removing it. This is the gateway's state, not the config's. A built-in cannot be disabled.",
    "POST /v1/gateway/servers/{name}/refresh": "Register an upstream again as it stands, to re-read its tools, and report what was added and removed. Its tools vanish briefly. If it fails, the old definition is put back.",
    "GET /v1/gateway/groups": "Tool groups.",
    "PUT /v1/gateway/groups/{name}": "Create or replace a tool group.",
    "DELETE /v1/gateway/groups/{name}": "Remove a tool group.",
    "GET /v1/gateway/tools": "Tools the gateway serves.",
    "POST /v1/gateway/tools/{name}/enable": "Put a disabled tool back in service.",
    "POST /v1/gateway/tools/{name}/disable": "Take one tool out of service. A refresh keeps it disabled.",
    "GET /v1/config": "The config the seed applies.",
    "GET /v1/config/validate": "What is wrong with `servers.json`, if anything. Changes nothing.",
    "GET /v1/config/diff": "What `apply` would do now: servers to register and remove, groups to rebuild, removals blocked by a group, and servers the config does not manage. Names only.",
    "POST /v1/config/apply": "Apply `servers.json`: registers what differs, and rebuilds groups when a server was registered or a group changed.",
    "GET /v1/tokens/agent": "The client token for agents, and the gateway's mode.",
    "GET /v1/catalogue/marketplaces": "Marketplaces in `marketplaces.json`, with how many plugins each has indexed.",
    "POST /v1/catalogue/marketplaces": "Add a marketplace: `repo`, and optionally `name` and `ref`. Starts a full sync, so a repo that cannot be read shows in `/v1/status`.",
    "DELETE /v1/catalogue/marketplaces/{name}": "Remove a marketplace. Starts a full sync, which drops its plugins from the index.",
    "POST /v1/catalogue/sync": "Start a sync in the background. An empty body is a quick pass: only what moved is reindexed. Optional: `plugin`, `force`, `full`, and `gateway` to refresh the gateway's servers and tools instead.",
    "GET /v1/catalogue/records/{id:path}": "One record in full, by id, such as `skill:my-plugin/my-skill`.",
    "GET /v1/embeddings/pending": "Records with no vector, roughly how many tokens they would cost, and the last run.",
    "POST /v1/embeddings/run": "Start embedding what is pending, in the background. This can spend money: read `/v1/embeddings/pending` first.",
    "POST /v1/embeddings": "Embed `input` with `model`, OpenAI style. Every embedding goes through here.",
    "POST /v1/search": "Search skills and tools: `query`, and optionally `kind`, `plugin`, `marketplace`, `limit`, `full`, `budget_ms`, `trace`. The same pipeline and reply as `catalogue__search`.",
    "POST /v1/skills/mcp": "The MCP Skills extension as JSON-RPC: `initialize`, `skills/list`, `skills/get`, `resources/read`. The gateway rejects these methods, so they are served here.",
    "POST /v1/storage/stage": "Stage a file and return a presigned URL. Needs the `storage` profile.",
    "GET /v1/storage/buckets": "Staging buckets. Needs the `storage` profile.",
    "GET /v1/backup": "Snapshots at the backup target, newest first.",
    "POST /v1/backup": "Start `backup-data` alone, with no `restore-test` after it. Poll `/v1/jobs/backup-data/runs`. A 409 if no target is set.",
    "GET /v1/backup/config": "The backup target, which holds no secrets, and whether `RESTIC_PASSWORD` is set. A 409 if no target is set.",
    "GET /v1/backup/plan": "What `canopy restore` would bring back and overwrite. Changes nothing: a restore stops the stack, so it runs from the host.",
    "GET /v1/context": "The user's context records (habits, SOPs, preferences, background notes) in `agent-data/context`, with the repo's `head` and `remote`.",
    "POST /v1/context/push": "Pull the `CONTEXT_REMOTE` with rebase, then push. The `context-push` job runs this hourly when a remote is set. A 409 if the remote does not rebase: nothing is forced.",
    "GET /v1/context/{kind}/{name}": "One record's markdown. `kind` is habit, sop, preference or context.",
    "PUT /v1/context/{kind}/{name}": "Create or replace a record and commit it, then reindex. JSON `text`, and `description` if the text has no frontmatter.",
    "DELETE /v1/context/{kind}/{name}": "Remove a record, as a commit, then reindex.",
    "GET /v1/jobs": "Scheduled jobs and their last runs.",
    "GET /v1/jobs/{name}/runs": "Recent runs of a job.",
    "POST /v1/jobs/{name}/run": "Start a job in the background. Poll its runs for the result.",
}


class _Stub:
    mode = "enterprise"


def routes() -> list[str]:
    found = []
    for r in api.create_app(_Stub(), "unused").routes:
        for m in sorted((r.methods or set()) - {"HEAD"}):
            found.append(f"{m} {r.path}")
    return found


def page() -> str:
    found = routes()
    missing = [r for r in found if r not in ROUTES]
    gone = [r for r in ROUTES if r not in found]
    if missing or gone:
        sys.exit(f"undescribed routes: {missing}; described but gone: {gone}")
    rows = "\n".join(f"| `{r.split()[0]}` | `{r.split()[1]}` | {ROUTES[r]} |" for r in found)
    return ("# API reference\n\nGenerated by `scripts/gen-api-docs.py`: do not edit by hand.\n\n"
            "Every route except `/health` needs `Authorization: Bearer <token>`, from\n"
            "`data/api/token`. The `canopy` CLI wraps these routes.\n\n"
            "| Method | Route | Does |\n| --- | --- | --- |\n" + rows + "\n")


if __name__ == "__main__":
    OUT.write_text(page())
