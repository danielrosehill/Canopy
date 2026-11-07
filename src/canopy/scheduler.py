"""One scheduler, inside `api`. Jobs are defined in `agent-data/config/jobs.json`:

    {"jobs": [{"name": "sync", "every": "1h"}, {"name": "embed", "after": "sync"}]}

`every` is a number and m, h or d. `after` runs a job when another succeeds. `enabled` defaults to
true. Each run is a row in `system.job_runs`. The partial unique index on that table is the lock, so
two runs of a job never overlap. A job is due when its interval has passed since its last start,
so a run missed while the stack was down starts soon after boot.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import store

CONFIG = Path(os.environ.get("CANOPY_CONFIG", "/config"))
TICK = int(os.environ.get("SCHEDULER_TICK_SEC", "30"))
START_DELAY = int(os.environ.get("SCHEDULER_START_DELAY_SEC", "60"))   # the seed makes the tokens
OUTPUT_CAP = 10_000
OVERDUE_SLACK = 600   # seconds past two intervals, as the catalogue judges a late sync
INTERRUPTED = "interrupted by a restart"
# Embedding can cost money, so unattended runs stay off unless asked for.
EMBED_ON_SYNC = os.environ.get("EMBED_ON_SYNC", "false").lower() == "true"
# A refresh deletes and registers each server again, so its tools vanish briefly. Off unless asked for.
TOOL_REFRESH = os.environ.get("CANOPY_TOOL_REFRESH", "false").lower() == "true"

def defaults(config: Path) -> list[dict]:
    """Backups start once `backup.json` names a target. `backup-config` pushes only what changed."""
    target = (config / "backup.json").is_file()
    return [{"name": "sync", "every": "1h"}, {"name": "sync-recent", "every": "5m"}, {"name": "embed", "after": "sync", "enabled": EMBED_ON_SYNC},
            {"name": "health", "every": "5m"}, {"name": "backup-data", "every": "1d", "enabled": target},
            {"name": "backup-config", "every": "1h", "enabled": target},
            {"name": "restore-test", "after": "backup-data", "enabled": target},
            {"name": "storage-expiry", "every": "1d"}, {"name": "tool-refresh", "every": "1d", "enabled": TOOL_REFRESH}]


_UNITS = {"m": 60, "h": 3600, "d": 86400}


def seconds(span: str) -> int:
    m = re.fullmatch(r"([1-9]\d*)([mhd])", span) if isinstance(span, str) else None
    if not m:
        raise ValueError(f"not an interval, use a number and m, h or d: {span!r}")
    return int(m[1]) * _UNITS[m[2]]


@dataclass(frozen=True)
class Job:
    name: str
    every: int | None = None
    after: str | None = None
    enabled: bool = True


def load(config: Path = CONFIG) -> list[Job]:
    path = config / "jobs.json"
    raw = json.loads(path.read_text())["jobs"] if path.is_file() else defaults(config)
    jobs = []
    for j in raw:
        if bool(j.get("every")) == bool(j.get("after")):
            raise ValueError(f"job {j.get('name')!r} needs exactly one of `every` and `after`")
        jobs.append(Job(j["name"], seconds(j["every"]) if j.get("every") else None, j.get("after"),
                        j.get("enabled", True)))
    names = [j.name for j in jobs]
    if len(set(names)) != len(names):
        raise ValueError("two jobs share a name")
    for j in jobs:
        if j.after and j.after not in names:
            raise ValueError(f"job {j.name!r} runs after {j.after!r}, which does not exist")
    return jobs


def interval(name: str, default: int, config: Path = CONFIG) -> int:
    """How often a job is meant to run, for whoever judges whether it is overdue."""
    try:
        return next((j.every for j in load(config) if j.name == name and j.every), default)
    except (ValueError, KeyError, OSError):
        return default


class Scheduler:
    def __init__(self, db: store.Db, actions: dict[str, Callable[[], str]], config: Path = CONFIG,
                 clock: Callable[[], float] = time.time):
        self.db, self.actions, self.config, self.clock = db, actions, config, clock
        self.ticked = clock()   # the loop's last sign of life

    def jobs(self) -> dict[str, Job]:
        return {j.name: j for j in load(self.config)}

    def recover(self) -> int:
        """Close runs a restart cut short, or their lock would hold for ever."""
        return len(self.db.execute(
            "UPDATE system.job_runs SET finished = now(), ok = FALSE, output = %s "
            "WHERE finished IS NULL RETURNING id", (INTERRUPTED,)).fetchall())

    def _last_started(self, name: str) -> float | None:
        row = self.db.execute("SELECT extract(epoch FROM max(started))::float8 AS at FROM system.job_runs "
                              "WHERE job = %s", (name,)).fetchone()
        return row["at"]

    def due(self) -> list[str]:
        out = []
        for j in self.jobs().values():
            if j.enabled and j.every and j.name in self.actions:
                last = self._last_started(j.name)
                if last is None or self.clock() - last >= j.every:
                    out.append(j.name)
        return out

    def run(self, name: str) -> dict:
        """Run one job now, then what follows it. `started` is False if it was already running."""
        job = self.jobs().get(name)
        if job is None or name not in self.actions:
            raise KeyError(name)
        row = self.db.execute(
            "INSERT INTO system.job_runs (job, started) VALUES (%s, to_timestamp(%s)) "
            "ON CONFLICT (job) WHERE finished IS NULL DO NOTHING RETURNING id", (name, self.clock())).fetchone()
        if row is None:
            return {"job": name, "started": False}
        ok, output = True, ""
        try:
            output = self.actions[name]() or ""
        except Exception as e:  # noqa: BLE001 - a failed job is a result, recorded like any other
            ok, output = False, f"{type(e).__name__}: {e}"
        self.db.execute("UPDATE system.job_runs SET finished = now(), ok = %s, output = %s WHERE id = %s",
                        (ok, output[:OUTPUT_CAP], row["id"]))
        result = {"job": name, "started": True, "ok": ok, "output": output[:OUTPUT_CAP]}
        if ok:
            followers = [j.name for j in self.jobs().values()
                         if j.after == name and j.enabled and j.name in self.actions]
            if followers:
                result["then"] = [self.run(f) for f in followers]
        return result

    def runs(self, name: str, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, job, started::text, finished::text, ok, output, "
            "extract(epoch FROM finished - started)::float8 AS seconds FROM system.job_runs "
            "WHERE job = %s ORDER BY id DESC LIMIT %s", (name, limit)).fetchall()
        return [dict(r) for r in rows]

    def listing(self) -> list[dict]:
        out = []
        for j in self.jobs().values():
            last = self.runs(j.name, 1)
            out.append({"name": j.name, "every_sec": j.every, "after": j.after, "enabled": j.enabled,
                        "runnable": j.name in self.actions, "last": last[0] if last else None})
        return out

    def check(self) -> dict:
        """For /v1/status: a job that fails or stops running is a fault in the stack, even with every
        service up. Two kinds of run do not count. `health` calls this, so its own failure would keep it red for
        ever. And a run cut off by a restart says nothing about the job: counting it would turn every
        `canopy update` that lands during a job into a rollback."""
        now, jobs = self.clock(), self.jobs()
        last = {r["job"]: r for r in self.db.execute(
            "SELECT DISTINCT ON (job) job, ok, left(output, 300) AS output FROM system.job_runs "
            "WHERE finished IS NOT NULL AND output IS DISTINCT FROM %s ORDER BY job, id DESC", (INTERRUPTED,))}
        good = {r["job"]: r["at"] for r in self.db.execute(
            "SELECT job, extract(epoch FROM max(started))::float8 AS at FROM system.job_runs "
            "WHERE ok GROUP BY job")}
        watched = [j for j in jobs.values() if j.enabled and j.name in self.actions and j.name != "health"]
        failing = {j.name: last[j.name]["output"] for j in watched
                   if j.name in last and not last[j.name]["ok"]}
        overdue = sorted(j.name for j in watched if j.every and j.name in good
                         and now - good[j.name] > 2 * j.every + OVERDUE_SLACK)
        stalled = now - self.ticked > START_DELAY + 10 * TICK
        out = {"ok": not (failing or overdue or stalled), "failing": failing, "overdue": overdue,
               "backup": self._backup_age(jobs, good, last, now)}
        if stalled:
            out["error"] = f"the scheduler has not ticked for {now - self.ticked:.0f}s"
        return out

    def _backup_age(self, jobs: dict[str, Job], good: dict, last: dict, now: float) -> dict:
        data = jobs.get("backup-data")
        if data is None or not data.enabled:
            return {"state": "off"}
        at = good.get("backup-data")
        test = last.get("restore-test")
        return {"state": "on", "last_ok_hours": round((now - at) / 3600, 1) if at else None,
                "restore_test_ok": test["ok"] if test else None}

    def tick(self) -> list[threading.Thread]:
        """Each job gets a thread, so a long sync does not hold up the rest."""
        threads = [threading.Thread(target=self.run, args=(n,), daemon=True) for n in self.due()]
        for t in threads:
            t.start()
        return threads

    def loop(self) -> None:
        self.recover()
        time.sleep(START_DELAY)
        while True:
            self.ticked = self.clock()
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - a bad jobs.json must not end the loop
                print(f"scheduler: {e!r}", flush=True)
            time.sleep(TICK)


# --- what the jobs do ----------------------------------------------------------------

class CatalogueJobs:
    """Both calls return at once and work in the background, so each waits for the real end."""

    def __init__(self, db: store.Db, call: Callable[[str, dict], str], wait: float = 3600, poll: float = 2):
        self.db, self.call, self.wait, self.poll = db, call, wait, poll

    def sync(self) -> str:
        # A new row in `runs` is written only when a pass ends. The `running` flag is set inside the
        # catalogue's thread, so reading it straight after asking can look like a finished sync.
        before = self.db.execute("SELECT coalesce(max(id), 0) AS id FROM catalogue.runs").fetchone()["id"]
        self.call("catalogue__sync", {"full": True})
        deadline = time.time() + self.wait
        while time.time() < deadline:
            row = self.db.execute("SELECT ok, summary FROM catalogue.runs WHERE id > %s ORDER BY id LIMIT 1",
                                  (before,)).fetchone()
            if row:
                if not row["ok"]:
                    raise RuntimeError(row["summary"][:2000])
                return row["summary"]
            time.sleep(self.poll)
        raise TimeoutError(f"no sync finished within {self.wait} s")

    def sync_recent(self) -> str:
        """Fire and forget. With nothing pushed a quick pass writes no `runs` row to wait for."""
        return self.call("catalogue__sync", {})

    def embed(self) -> str:
        state = json.loads(self.call("catalogue__embed", {"dry_run": True}))
        if not state["pending"]["records"]:
            return "nothing pending"
        started = json.loads(self.call("catalogue__embed", {"dry_run": False}))
        if started.get("reason"):
            raise RuntimeError(started["reason"])
        deadline, began, seen = time.time() + self.wait, time.time(), False
        while time.time() < deadline:
            state = json.loads(self.call("catalogue__embed", {"dry_run": True}))
            seen = seen or state["running"]
            if not state["running"] and (seen or time.time() - began > 5 * self.poll):
                last = state.get("last") or {}
                if last.get("error"):
                    raise RuntimeError(last["error"])
                return json.dumps(last)
            time.sleep(self.poll)
        raise TimeoutError(f"embedding did not finish within {self.wait} s")
