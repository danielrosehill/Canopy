"""Timers and reminders that fire at their time, owned by canopy-voice.

A timer is kept in a JSON file under the voice data dir, so it survives a restart. When it is due, the
speaker's delivery calls run: gateway tools called through the speaker's own group (a phone push, a spoken
message), with the timer's text templated into their arguments. Which tools, and with what arguments, is
config (`timers` in voice.yaml), so nothing here knows how a household's phones are reached.

The file is the source of truth and is re-read on every tick, so a timer added by another process (the
`ask` command in the same container) is seen within `poll_s`. Read-modify-write holds an fcntl lock.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

from loguru import logger

HISTORY = 50          # fired or missed timers kept in the file, newest last


@dataclass
class Timer:
    id: str
    speaker: str
    kind: str             # "timer" | "reminder"
    due: float            # epoch seconds
    text: str             # the timer's label, or what to be reminded of
    created: float = field(default_factory=time.time)
    status: str = "pending"   # pending | fired | failed | missed | cancelled
    result: str = ""
    fired_at: float = 0.0


class TimerStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextlib.contextmanager
    def edit(self):
        """Yields the document ({"pending": [...], "history": [...]}) under the lock, and writes it back."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(self.path.suffix + ".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            doc = self._read()
            yield doc
            doc["history"] = doc["history"][-HISTORY:]
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(to_json(doc), indent=1, ensure_ascii=False))
            os.replace(tmp, self.path)

    def _read(self) -> dict:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as e:
            logger.error(f"timers {self.path}: unreadable ({e}), starting empty")
            raw = {}
        return {"pending": [Timer(**t) for t in raw.get("pending", [])],
                "history": [Timer(**t) for t in raw.get("history", [])]}

    def read(self) -> dict:
        with self.edit() as doc:
            return doc


def to_json(doc: dict) -> dict:
    return {k: [asdict(t) for t in v] for k, v in doc.items()}


def parse_at(at: str, now: datetime) -> datetime:
    """A local wall-clock time: "YYYY-MM-DD HH:MM" (or ISO with T, seconds optional), or "HH:MM" for its
    next occurrence. `now` is timezone-aware and gives the zone."""
    at = at.strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", at)
    if m:
        t = now.replace(hour=int(m[1]), minute=int(m[2]), second=0, microsecond=0)
        return t if t > now else t + timedelta(days=1)
    try:
        t = datetime.fromisoformat(at.replace(" ", "T", 1))
    except ValueError:
        raise ValueError(f"`at` {at!r}: expected \"YYYY-MM-DD HH:MM\" or \"HH:MM\", local time")
    return t.replace(tzinfo=now.tzinfo) if t.tzinfo is None else t


def spoken_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    parts = [f"{n} {unit}{'' if n == 1 else 's'}" for n, unit in ((h, "hour"), (m, "minute"), (s, "second")) if n]
    return " ".join(parts) or "0 seconds"


def fill(value, values: dict):
    """Template a delivery call's arguments: `{name}` in any string is replaced by values[name]."""
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m[1], m[0])), value)
    if isinstance(value, dict):
        return {k: fill(v, values) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, values) for v in value]
    return value


Deliver = Callable[[Timer], Awaitable[tuple[bool, str]]]


class Scheduler:
    """Adds, lists and cancels a speaker's timers, and fires each when due through `deliver`."""

    def __init__(self, store: TimerStore, tz: ZoneInfo, deliver: Deliver, max_seconds: int = 7 * 86400,
                 late_grace_s: int = 3600, poll_s: float = 15, clock: Callable[[], float] = time.time):
        self.store, self.tz, self.deliver = store, tz, deliver
        self.max_seconds, self.late_grace_s, self.poll_s, self.clock = max_seconds, late_grace_s, poll_s, clock
        self.wake = asyncio.Event()

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), self.tz)

    def _add(self, speaker: str, kind: str, due: float, text: str) -> Timer:
        if due - self.clock() > self.max_seconds:
            raise ValueError(f"too far ahead: at most {spoken_duration(self.max_seconds)}")
        if due <= self.clock():
            raise ValueError("that time has already passed")
        t = Timer(id=uuid.uuid4().hex[:6], speaker=speaker, kind=kind, due=due, text=text.strip(),
                  created=self.clock())
        with self.store.edit() as doc:
            doc["pending"].append(t)
        self.wake.set()
        logger.info(f"timer {t.id} set for {speaker}: {kind} {text!r} at {self.local(t.due)}")
        return t

    def set_timer(self, speaker: str, seconds: float, label: str = "") -> Timer:
        seconds = float(seconds)
        if seconds < 1:
            raise ValueError("`seconds` must be at least 1")
        return self._add(speaker, "timer", self.clock() + seconds, label)

    def set_reminder(self, speaker: str, at: str, text: str) -> Timer:
        if not text.strip():
            raise ValueError("`text` is required: what to remind them of")
        return self._add(speaker, "reminder", parse_at(at, self.now()).timestamp(), text)

    def pending(self, speaker: str) -> list[Timer]:
        return sorted((t for t in self.store.read()["pending"] if t.speaker == speaker), key=lambda t: t.due)

    def cancel(self, speaker: str, id: str = "", text: str = "", all: bool = False) -> list[Timer]:
        """Cancels by id, else by a word of its label/text, else every one if `all`. Returns what was cancelled."""
        with self.store.edit() as doc:
            mine = [t for t in doc["pending"] if t.speaker == speaker]
            if id:
                hit = [t for t in mine if t.id == id.strip()]
            elif text:
                hit = [t for t in mine if text.strip().lower() in t.text.lower()]
            elif all or len(mine) == 1:
                hit = mine
            else:
                hit = []
            for t in hit:
                t.status = "cancelled"
                doc["pending"].remove(t)
                doc["history"].append(t)
        return hit

    def local(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%a %H:%M:%S")

    def describe(self, t: Timer) -> dict:
        return {"id": t.id, "kind": t.kind, "text": t.text, "due_local": self.local(t.due),
                "remaining": spoken_duration(max(0, t.due - self.clock()))}

    async def tick(self) -> float:
        """Fires what is due; returns seconds until the next due timer (at most poll_s)."""
        now = self.clock()
        with self.store.edit() as doc:
            due = [t for t in doc["pending"] if t.due <= now]
            for t in due:
                doc["pending"].remove(t)
            later = [t.due for t in doc["pending"]]
        for t in due:
            late = now - t.due
            if late > self.late_grace_s:
                t.status, t.result = "missed", f"{spoken_duration(late)} late (canopy-voice was down), not delivered"
            else:
                try:
                    ok, t.result = await self.deliver(t)
                except Exception as e:     # a delivery bug must not stop the loop
                    ok, t.result = False, f"{type(e).__name__}: {e}"
                t.status = "fired" if ok else "failed"
            t.fired_at = self.clock()
            (logger.info if t.status == "fired" else logger.error)(
                f"timer {t.id} for {t.speaker} {t.status} ({t.kind} {t.text!r}, due {self.local(t.due)}, "
                f"late {late:.1f} s): {t.result[:300]}")
            with self.store.edit() as doc:
                doc["history"].append(t)
        return min([self.poll_s, *(d - self.clock() for d in later)])

    async def run(self) -> None:
        logger.info(f"timers: {self.store.path}, {len(self.store.read()['pending'])} pending")
        while True:
            try:
                wait = await self.tick()
            except Exception as e:
                logger.exception(f"timers: tick failed: {e}")
                wait = self.poll_s
            self.wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), max(0.05, wait))


TOOL_DOCS = {
    "set_timer": ("Start a countdown timer for the speaker. At the time their phone is alerted (and it may be "
                  "spoken), wherever they are. Use for \"set a timer for ten minutes\", \"remind me in twenty "
                  "minutes to check the oven\". Not a calendar event.",
                  {"seconds": {"type": "integer", "description": "Length in seconds"},
                   "label": {"type": "string", "description": "Optional: what it's for, a few words"}},
                  ["seconds"]),
    "set_reminder": ("Remind the speaker of something at a clock time today or within the next days: their phone "
                     "is alerted with the text at that time. Use for \"remind me at six to call the plumber\". "
                     "For a countdown (\"in 20 minutes\") use set_timer. Not a calendar event.",
                     {"at": {"type": "string", "description": "Local time: \"HH:MM\" (next occurrence) or "
                                                              "\"YYYY-MM-DD HH:MM\""},
                      "text": {"type": "string", "description": "What to remind them of"}},
                     ["at", "text"]),
    "list_timers": ("The speaker's running timers and reminders, with how long is left.", {}, []),
    "cancel_timer": ("Cancel one of the speaker's timers or reminders: by id (from list_timers), or by a word of its "
                     "label, or all of them. With one running and nothing named, cancels that one.",
                     {"id": {"type": "string"}, "label": {"type": "string", "description": "A word of its label/text"},
                      "all": {"type": "boolean"}},
                     []),
}


async def call_tool(scheduler: Scheduler, speaker: str, name: str, args: dict) -> dict:
    """Runs one of TOOL_DOCS for `speaker`. Returns a JSON-able result for the model; never raises."""
    try:
        if name == "set_timer":
            t = scheduler.set_timer(speaker, args.get("seconds", 0), str(args.get("label") or ""))
            return {"ok": True, "set": scheduler.describe(t)}
        if name == "set_reminder":
            t = scheduler.set_reminder(speaker, str(args.get("at") or ""), str(args.get("text") or ""))
            return {"ok": True, "set": scheduler.describe(t)}
        if name == "list_timers":
            return {"ok": True, "timers": [scheduler.describe(t) for t in scheduler.pending(speaker)]}
        if name == "cancel_timer":
            hit = scheduler.cancel(speaker, str(args.get("id") or ""), str(args.get("label") or ""),
                                   bool(args.get("all")))
            left = [scheduler.describe(t) for t in scheduler.pending(speaker)]
            return {"ok": bool(hit), "cancelled": [scheduler.describe(t) for t in hit], "still_running": left,
                    **({} if hit else {"error": "nothing matched; name it by id or label, or all"})}
        return {"ok": False, "error": f"unknown timer tool {name}"}
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": str(e)}
