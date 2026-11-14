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

    def sync(db, only=None, force=False, full=False):
        ran.append(("sync", only, force, full))
        if len(ran) == 1:
            release.wait(5)
        return {"ok": True, "changed": False}

    monkeypatch.setattr(ix, "sync", sync)
    monkeypatch.setattr(ix, "refresh_gateway_records",
                        lambda db: ran.append(("gateway",)) or {"ok": True, "changed": False})
    assert cat._request_sync(None, False, "full") is False   # starts the worker
    time.sleep(0.05)
    assert cat._request_sync("p", True, "quick") is True       # waits
    assert cat._request_sync(None, False, "gateway") is True
    assert cat._request_sync("p", True, "quick") is True       # same as one waiting: dropped
    assert cat._job["queued"] == 2
    release.set()
    wait_idle(cat)
    assert ran == [("sync", None, False, True), ("sync", "p", True, False), ("gateway",)]


def test_a_pass_that_changed_nothing_leaves_the_keyword_index_alone(cat, monkeypatch):
    refreshed = []
    monkeypatch.setattr(cat, "refresh_keywords", lambda: refreshed.append(1))
    monkeypatch.setattr(ix, "sync", lambda db, only=None, force=False, full=False: {"ok": True, "changed": False})
    cat._request_sync(None, False, "quick")
    wait_idle(cat)
    assert refreshed == []
    monkeypatch.setattr(ix, "sync", lambda db, only=None, force=False, full=False: {"ok": True, "changed": True})
    cat._request_sync(None, False, "quick")
    wait_idle(cat)
    assert refreshed == [1]


def test_the_sync_tool_picks_the_mode(cat, monkeypatch):
    asked = []
    monkeypatch.setattr(cat, "_request_sync", lambda only, force, mode: asked.append((only, force, mode)) or False)
    cat.sync()
    cat.sync(full=True)
    cat.sync(force=True)
    cat.sync(plugin="p", full=True)
    cat.sync(gateway=True)
    assert asked == [(None, False, "quick"), (None, False, "full"), (None, True, "full"),
                     ("p", False, "quick"), (None, False, "gateway")]


def test_a_crashed_pass_does_not_stop_the_queue(cat, monkeypatch):
    calls = []

    def sync(db, only=None, force=False, full=False):
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
