"""The catalogue's sync queue: requests made during a pass wait in order, and none is lost."""
import importlib
import threading
import time

import pytest

from canopy import index as ix


class NoDb:
    def execute(self, *a, **kw):
        raise AssertionError("no database in this test")


@pytest.fixture
def cat(monkeypatch):
    monkeypatch.setattr(ix, "connect", lambda: NoDb())
    import canopy.catalogue as c
    c = importlib.reload(c)
    monkeypatch.setattr(c, "refresh_keywords", lambda: None)
    return c


def wait_idle(c, seconds=5):
    deadline = time.time() + seconds
    while c._job["running"] and time.time() < deadline:
        time.sleep(0.01)
    assert not c._job["running"]


def test_requests_during_a_pass_run_in_order_and_duplicates_are_dropped(cat, monkeypatch):
    release, ran = threading.Event(), []

    def sync(db, only=None, force=False):
        ran.append(("sync", only, force))
        if len(ran) == 1:
            release.wait(5)
        return {"ok": True}

    monkeypatch.setattr(ix, "sync", sync)
    monkeypatch.setattr(ix, "sync_recent", lambda db: ran.append(("recent",)) or {"ok": True})
    assert cat._request_sync(None, False, False) is False   # starts the worker
    time.sleep(0.05)
    assert cat._request_sync("p", True, False) is True       # waits
    assert cat._request_sync(None, False, True) is True
    assert cat._request_sync("p", True, False) is True       # same as one waiting: dropped
    assert cat._job["queued"] == 2
    release.set()
    wait_idle(cat)
    assert ran == [("sync", None, False), ("sync", "p", True), ("recent",)]


def test_a_crashed_pass_does_not_stop_the_queue(cat, monkeypatch):
    calls = []

    def sync(db, only=None, force=False):
        calls.append(only)
        raise RuntimeError("boom")

    monkeypatch.setattr(ix, "sync", sync)
    monkeypatch.setattr(ix, "write_lock", threading.Lock())
    monkeypatch.setattr(cat, "db", type("D", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None,
                                                  "execute": lambda s, *a: None})())
    cat._request_sync("a")
    cat._request_sync("b")
    wait_idle(cat)
    assert "a" in calls and cat._job["last"]["ok"] is False
