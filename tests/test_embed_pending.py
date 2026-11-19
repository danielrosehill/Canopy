"""`embed_pending`: batches in parallel, a failed batch is skipped, a dead provider stops the run."""
import threading

import pytest

from canopy import index as ix


class FakeDb:
    def __init__(self, n):
        self.rows = [{"id": i, "kind": "skill", "plugin": "p", "name": f"s{i}", "description": "d",
                      "body": "b", "embed_key": f"k{i}"} for i in range(n)]
        self.written = []

    def execute(self, sql, params=()):
        if sql.startswith("UPDATE"):
            self.written.append(params[2])
            return None
        return type("R", (), {"fetchall": lambda s: self.rows})()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None


@pytest.fixture(autouse=True)
def lock(monkeypatch):
    monkeypatch.setattr(ix, "write_lock", threading.Lock())


def test_batches_run_at_once(monkeypatch):
    inflight, peak, gate = [0], [0], threading.Barrier(3, timeout=5)
    m = threading.Lock()

    def embed(texts):
        with m:
            inflight[0] += 1
            peak[0] = max(peak[0], inflight[0])
        gate.wait()   # all three batches must be in flight together to pass
        with m:
            inflight[0] -= 1
        return [[1.0, 0.0]] * len(texts)

    monkeypatch.setattr(ix, "embed", embed)
    db = FakeDb(9)
    assert ix.embed_pending(db, batch=3, parallel=3) == (9, None)
    assert peak[0] == 3 and sorted(db.written) == sorted(r["embed_key"] for r in db.rows)


def test_a_failed_batch_is_skipped_and_the_rest_are_written(monkeypatch):
    def embed(texts):
        if "s3" in texts[0]:
            raise RuntimeError("bad batch")
        return [[1.0]] * len(texts)

    monkeypatch.setattr(ix, "embed", embed)
    db = FakeDb(9)
    done, err = ix.embed_pending(db, batch=3, parallel=1, retry_sec=0)
    assert done == 6 and set(db.written) == {"k0", "k1", "k2", "k6", "k7", "k8"}
    assert "1 of 3 batches failed, 3 records still pending" in err and "bad batch" in err


def test_a_provider_that_is_down_stops_the_run(monkeypatch):
    calls = []

    def embed(texts):
        calls.append(1)
        raise RuntimeError("503")

    monkeypatch.setattr(ix, "embed", embed)
    done, err = ix.embed_pending(FakeDb(100), batch=1, parallel=1, retry_sec=0)
    assert done == 0 and "provider looks down" in err
    assert len(calls) == 6   # three batches, each tried twice, then it stops
