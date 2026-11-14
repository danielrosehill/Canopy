import json

import pytest

from canopy import scheduler as s


def write(tmp_path, jobs):
    (tmp_path / "jobs.json").write_text(json.dumps({"jobs": jobs}))
    return tmp_path


@pytest.mark.parametrize("span,want", [("5m", 300), ("1h", 3600), ("7d", 604800)])
def test_intervals(span, want):
    assert s.seconds(span) == want


@pytest.mark.parametrize("span", ["0h", "5", "h", "1w", "-1m", 5, None])
def test_a_bad_interval_is_refused(span):
    with pytest.raises(ValueError):
        s.seconds(span)


def test_no_file_means_the_defaults_and_they_do_not_spend_money(tmp_path):
    jobs = {j.name: j for j in s.load(tmp_path)}
    assert jobs["sync"].every == 3600 and jobs["health"].every == 300
    assert jobs["embed"].after == "sync" and jobs["embed"].enabled is False


@pytest.mark.parametrize("jobs,message", [
    ([{"name": "a"}], "exactly one"),
    ([{"name": "a", "every": "1h", "after": "b"}, {"name": "b", "every": "1h"}], "exactly one"),
    ([{"name": "a", "after": "gone"}], "does not exist"),
    ([{"name": "a", "every": "1h"}, {"name": "a", "every": "2h"}], "share a name"),
    (["a"], "needs a `name`"),
    ([{"every": "1h"}], "needs a `name`"),
])
def test_a_bad_file_is_refused(tmp_path, jobs, message):
    with pytest.raises(ValueError, match=message):
        s.load(write(tmp_path, jobs))


def test_interval_reads_the_file_and_survives_a_broken_one(tmp_path):
    assert s.interval("sync", 99, write(tmp_path, [{"name": "sync", "every": "6h"}])) == 21600
    (tmp_path / "jobs.json").write_text("{not json")
    with pytest.raises(ValueError, match="not JSON"):
        s.load(tmp_path)
    assert s.interval("sync", 99, tmp_path) == 99


def test_a_bad_jobs_file_is_a_named_error(tmp_path):
    (tmp_path / "jobs.json").write_text('{"jobs": [')
    with pytest.raises(ValueError, match="jobs.json is not JSON"):
        s.load(tmp_path)
    (tmp_path / "jobs.json").write_text('[]')
    with pytest.raises(ValueError, match="`jobs` list"):
        s.load(tmp_path)


class FlakyDb:
    """Fails the first `fails` writes, as a database that blips would."""

    def __init__(self, fails):
        self.fails, self.writes = fails, []

    def execute(self, sql, params=()):
        if self.fails:
            self.fails -= 1
            raise OSError("connection lost")
        self.writes.append(params)


def test_a_run_is_closed_even_if_the_first_write_fails(monkeypatch):
    monkeypatch.setattr(s.time, "sleep", lambda _: None)
    db = FlakyDb(fails=2)
    s.Scheduler(db, {})._finish(7, True, "done")
    assert db.writes == [(True, "done", 7)]
