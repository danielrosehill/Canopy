"""Bring vectors from an older SQLite index into `catalogue.records`, so a move to Postgres does not
need a full re-embed.

    python -m canopy.import_vectors /path/to/catalogue.db

Run it after a first full sync: a vector lands on the records whose `embed_key` matches, so with no
records there is nothing to land on. Records it cannot fill stay pending, for `embed_pending`.

The file is opened read-only, and SQLite keeps recent writes in `catalogue.db-wal`, so copy that
file along with it, or an older state is read with no error. A vector counts if its label is the
canonical model or what `system.model_map` says a provider calls it. A record that already has a
vector from the current model is left alone.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np

from . import store
from .index import EMBED_MODEL

DIMENSIONS = 1024   # catalogue.records.embedding
BATCH = 500


def labels(db: store.Db, model: str) -> set[str]:
    rows = db.execute("SELECT provider_model FROM system.model_map WHERE model = %s", (model,))
    return {model} | {r["provider_model"] for r in rows}


def vector(blob: bytes) -> np.ndarray | None:
    """The stored bytes as a unit vector, or None if they are not a usable one."""
    if len(blob) != DIMENSIONS * 4:
        return None
    a = np.frombuffer(blob, dtype=np.float32)
    norm = float(np.linalg.norm(a))
    return a / norm if np.isfinite(norm) and norm > 0 else None


def import_vectors(db: store.Db, path: str | Path, model: str = EMBED_MODEL) -> dict:
    if not Path(path).is_file():
        raise ValueError(f"no such file: {path}")
    if not db.execute("SELECT count(*) AS n FROM records").fetchone()["n"]:
        raise ValueError("no records yet: run a full sync first")

    accepted = labels(db, model)
    report = {"source_vectors": 0, "other_model": 0, "unusable": 0, "no_record": 0, "kept": 0,
              "records_filled": 0}
    source = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    try:
        batch: list[tuple[str, np.ndarray]] = []
        for key, label, blob in source.execute("SELECT key, model, vec FROM embeddings"):
            report["source_vectors"] += 1
            if label not in accepted:
                report["other_model"] += 1
            elif (v := vector(blob)) is None:
                report["unusable"] += 1
            else:
                batch.append((key, v))
            if len(batch) >= BATCH:
                _write(db, batch, model, report)
                batch = []
        _write(db, batch, model, report)
    finally:
        source.close()
    report["still_pending"] = db.execute(
        "SELECT count(*) AS n FROM records WHERE embedding IS NULL OR embed_model IS DISTINCT FROM %s",
        (model,)).fetchone()["n"]
    return report


def _write(db: store.Db, batch: list[tuple[str, np.ndarray]], model: str, report: dict) -> None:
    with db:
        for key, v in batch:
            filled = db.execute(
                "UPDATE records SET embedding = %s, embed_model = %s WHERE embed_key = %s "
                "AND (embedding IS NULL OR embed_model IS DISTINCT FROM %s) RETURNING id",
                (v, model, key, model)).fetchall()
            report["records_filled"] += len(filled)
            if not filled:
                known = db.execute("SELECT 1 FROM records WHERE embed_key = %s LIMIT 1", (key,)).fetchone()
                report["kept" if known else "no_record"] += 1


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    try:
        print(json.dumps(import_vectors(store.Db(size=2), sys.argv[1]), indent=1))
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
