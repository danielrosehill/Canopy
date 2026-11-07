"""The scheduler on the real database: the lock, recovery and the record of each run."""
from __future__ import annotations

import json
import threading

import pytest

from canopy import catalogue as cat
from canopy import scheduler as s


@pytest.fixture(autouse=True)
def clean():
    cat.db.execute("TRUNCATE system.job_runs, catalogue.runs")


def make(tmp_path, actions, jobs=None, clock=None):
    (tmp_path / "jobs.json").write_text(json.dumps({"jobs": jobs or [{"name": n, "every": "1h"} for n in actions]}))
    return s.Scheduler(cat.db, actions, tmp_path, **({"clock": clock} if clock else {}))


def test_a_run_is_recorded_with_its_result(tmp_path):
    sch = make(tmp_path, {"a": lambda: "done"})
    assert sch.run("a")["ok"] is True
    last = sch.runs("a")[0]
    assert last["ok"] is True and last["output"] == "done" and last["finished"] and last["seconds"] >= 0


def test_a_failure_is_recorded_and_stops_what_follows(tmp_path):
    ran = []

    def boom():
        raise RuntimeError("no")
    sch = make(tmp_path, {"a": boom, "b": lambda: ran.append("b")},
               [{"name": "a", "every": "1h"}, {"name": "b", "after": "a"}])
    assert sch.run("a")["ok"] is False
    assert sch.runs("a")[0]["output"] == "RuntimeError: no"
    assert ran == []


def test_a_follower_runs_after_success_unless_disabled(tmp_path):
    ran = []
    sch = make(tmp_path, {"a": lambda: "", "b": lambda: ran.append("b"), "c": lambda: ran.append("c")},
               [{"name": "a", "every": "1h"}, {"name": "b", "after": "a"},
                {"name": "c", "after": "a", "enabled": False}])
    sch.run("a")
    assert ran == ["b"]


def test_two_runs_of_a_job_never_overlap(tmp_path):
    entered, release, calls = threading.Event(), threading.Event(), []

    def slow():
        calls.append(1)
        entered.set()
        assert release.wait(10)
    sch = make(tmp_path, {"a": slow})
    t = threading.Thread(target=sch.run, args=("a",))
    t.start()
    assert entered.wait(10)
    assert sch.run("a") == {"job": "a", "started": False}
    release.set()
    t.join(10)
    assert calls == [1]
    assert sch.run("a")["started"] is True   # the lock went with the run


def test_a_restart_frees_the_lock_and_marks_the_run(tmp_path):
    sch = make(tmp_path, {"a": lambda: "ok"})
    cat.db.execute("INSERT INTO system.job_runs (job, started) VALUES ('a', now())")
    assert sch.run("a")["started"] is False
    assert sch.recover() == 1
    assert sch.runs("a")[0]["ok"] is False and "restart" in sch.runs("a")[0]["output"]
    assert sch.run("a")["ok"] is True


def test_due_follows_the_interval_and_a_missed_run_is_due_at_once(tmp_path):
    now = [1_800_000_000.0]
    sch = make(tmp_path, {"a": lambda: ""}, clock=lambda: now[0])
    assert sch.due() == ["a"]          # never run
    sch.run("a")
    now[0] += 3599
    assert sch.due() == []
    now[0] += 1
    assert sch.due() == ["a"]
    now[0] += 86400 * 3                # the stack was down
    assert sch.due() == ["a"]


def test_disabled_following_and_unknown_jobs_are_never_due(tmp_path):
    sch = make(tmp_path, {"a": lambda: "", "b": lambda: ""},
               [{"name": "a", "every": "1h", "enabled": False}, {"name": "b", "after": "a"},
                {"name": "later", "every": "1h"}])
    assert sch.due() == []
    listed = {j["name"]: j for j in sch.listing()}
    assert listed["later"]["runnable"] is False and listed["a"]["last"] is None


def test_sync_job_waits_for_the_pass_to_end_and_reports_it(tmp_path):
    def call(name, args):
        def finish():
            cat.db.execute("INSERT INTO catalogue.runs (started, finished, ok, summary) "
                           "VALUES (1, 2, TRUE, '{\"ok\": true}')")
        threading.Timer(0.4, finish).start()
        return "{}"
    jobs = s.CatalogueJobs(cat.db, call, wait=5, poll=0.05)
    started = threading.Event()
    out = []
    t = threading.Thread(target=lambda: (started.set(), out.append(jobs.sync())))
    t.start()
    started.wait()
    t.join(0.2)
    assert t.is_alive(), "returned before the pass ended"
    t.join(5)
    assert out == ['{"ok": true}']


def test_sync_job_ignores_an_earlier_pass_and_fails_on_a_failed_one(tmp_path):
    cat.db.execute("INSERT INTO catalogue.runs (started, finished, ok, summary) VALUES (1, 2, TRUE, 'old')")

    def call(name, args):
        cat.db.execute("INSERT INTO catalogue.runs (started, finished, ok, summary) VALUES (3, 4, FALSE, 'broke')")
        return "{}"
    with pytest.raises(RuntimeError, match="broke"):
        s.CatalogueJobs(cat.db, call, wait=5, poll=0.05).sync()


def test_sync_job_times_out_rather_than_passing(tmp_path):
    with pytest.raises(TimeoutError):
        s.CatalogueJobs(cat.db, lambda n, a: "{}", wait=0.2, poll=0.05).sync()


def embed_replies(*states):
    states = list(states)
    return lambda name, args: json.dumps(states.pop(0) if len(states) > 1 else states[0])


def test_embed_job_spends_nothing_when_nothing_is_pending():
    calls = []

    def call(name, args):
        calls.append(args)
        return json.dumps({"pending": {"records": 0}, "running": False})
    assert s.CatalogueJobs(cat.db, call).embed() == "nothing pending"
    assert calls == [{"dry_run": True}]


def test_embed_job_reports_an_error_from_the_run():
    pending = {"pending": {"records": 3}, "running": False, "last": None}
    call = embed_replies(pending, {"started": True}, {**pending, "running": True},
                         {**pending, "last": {"embedded": 1, "error": "stopped"}})
    with pytest.raises(RuntimeError, match="stopped"):
        s.CatalogueJobs(cat.db, call, wait=5, poll=0.01).embed()


def test_embed_job_says_why_it_could_not_start():
    pending = {"pending": {"records": 3}, "running": False}
    call = embed_replies(pending, {"started": False, "reason": "EMBED_PROVIDER is not set"})
    with pytest.raises(RuntimeError, match="EMBED_PROVIDER"):
        s.CatalogueJobs(cat.db, call).embed()


# --- /v1/status ---------------------------------------------------------------------------

def boom():
    raise RuntimeError("no")


def test_a_failed_job_is_a_fault_until_it_next_works(tmp_path):
    results = [boom, lambda: "ok"]
    sch = make(tmp_path, {"a": lambda: results.pop(0)()})
    sch.run("a")
    out = sch.check()
    assert out["ok"] is False and out["failing"] == {"a": "RuntimeError: no"}
    sch.run("a")
    assert sch.check()["ok"] is True


def test_health_cannot_keep_itself_red(tmp_path):
    sch = make(tmp_path, {"health": boom})
    sch.run("health")
    assert sch.check()["ok"] is True


def test_a_run_cut_off_by_a_restart_is_not_a_failure(tmp_path):
    """Or `canopy update` would go back whenever it landed during a job."""
    sch = make(tmp_path, {"a": lambda: "ok"})
    sch.run("a")
    cat.db.execute("INSERT INTO system.job_runs (job, started) VALUES ('a', now())")
    assert sch.recover() == 1
    assert sch.runs("a")[0]["ok"] is False and sch.check()["ok"] is True


def test_a_job_with_no_recent_success_is_overdue(tmp_path):
    now = [1_800_000_000.0]
    sch = make(tmp_path, {"a": lambda: "ok"}, clock=lambda: now[0])
    sch.run("a")
    now[0] += 2 * 3600 + 500
    assert sch.check()["overdue"] == []
    sch.ticked = now[0] = now[0] + 200
    out = sch.check()
    assert out["overdue"] == ["a"] and out["ok"] is False


def test_a_stopped_scheduler_is_a_fault(tmp_path):
    now = [1_800_000_000.0]
    sch = make(tmp_path, {"a": lambda: "ok"}, clock=lambda: now[0])
    now[0] += s.START_DELAY + 10 * s.TICK + 1
    out = sch.check()
    assert out["ok"] is False and "ticked" in out["error"]


def test_backup_age_is_reported(tmp_path):
    now = [1_800_000_000.0]
    sch = make(tmp_path, {"backup-data": lambda: "ok", "restore-test": boom},
               [{"name": "backup-data", "every": "1d"}, {"name": "restore-test", "after": "backup-data"}],
               clock=lambda: now[0])
    sch.run("backup-data")
    now[0] += 7200
    sch.ticked = now[0]
    out = sch.check()
    assert out["backup"] == {"state": "on", "last_ok_hours": 2.0, "restore_test_ok": False}
    assert out["failing"].keys() == {"restore-test"}


def test_a_stack_with_no_backup_target_says_so(tmp_path):
    assert make(tmp_path, {"a": lambda: "ok"}).check()["backup"] == {"state": "off"}


def test_a_job_can_run_without_what_follows_it(tmp_path):
    ran = []
    sch = make(tmp_path, {"a": lambda: "ok", "b": lambda: ran.append("b")},
               [{"name": "a", "every": "1h"}, {"name": "b", "after": "a"}])
    sch.run("a", follow=False)
    assert ran == []

