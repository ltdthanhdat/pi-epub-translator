#!/usr/bin/env python3
"""Durable state and EPUB mechanics for the project-local Pi extension."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
import uuid
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
import xml.etree.ElementTree as ET


LEASE_SECONDS = 120


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    xhtml_path: str
    locator: str
    input_path: str
    lease_token: str
    attempts: int
    backend: str
    model: str
    thinking: str
    target_language: str
    target_language_code: str
    glossary_path: str

    @classmethod
    def from_row(cls, row: sqlite3.Row, config: dict[str, object]) -> "ClaimedJob":
        return cls(
            id=row["id"],
            xhtml_path=row["xhtml_path"],
            locator=row["locator"],
            input_path=row["input_path"],
            lease_token=row["lease_token"],
            attempts=row["attempts"],
            backend=str(config.get("backend", "")),
            model=str(config.get("model", "")),
            thinking=str(config.get("thinking", "")),
            target_language=str(config.get("target_language", "")),
            target_language_code=str(config.get("target_language_code", "")),
            glossary_path=str(config.get("glossary_path", "")),
        )

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class RunStore:
    """Small SQLite-backed state machine shared by extension and workers."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    config_json TEXT NOT NULL DEFAULT '{}',
                    state TEXT NOT NULL DEFAULT 'running',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    merge_state TEXT NOT NULL DEFAULT 'pending',
                    merge_worker_id TEXT,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    output_path TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY,
                    xhtml_path TEXT NOT NULL,
                    locator TEXT NOT NULL,
                    input_path TEXT NOT NULL,
                    result_path TEXT,
                    state TEXT NOT NULL DEFAULT 'pending',
                    worker_id TEXT,
                    lease_token TEXT,
                    lease_until INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    UNIQUE(xhtml_path, locator)
                );
                CREATE INDEX IF NOT EXISTS jobs_state_lease_idx
                    ON jobs(state, lease_until);
                """
            )

    def connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 30000")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _config(self, db: sqlite3.Connection | None = None) -> dict[str, object]:
        owns = db is None
        if owns:
            db = self.connection()
        try:
            row = db.execute("SELECT config_json FROM runs WHERE id=1").fetchone()
            if row is None:
                raise ValueError("run has not been created")
            return json.loads(row["config_json"])
        finally:
            if owns:
                db.close()

    def create_run(self, config: dict[str, object], now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        normalized = dict(config)
        normalized.setdefault("max_attempts", 3)
        with self.connection() as db:
            db.execute(
                """INSERT INTO runs
                   (id, config_json, state, cancel_requested, merge_state, started_at, updated_at)
                   VALUES (1, ?, 'running', 0, 'pending', ?, ?)""",
                (json.dumps(normalized, ensure_ascii=False, sort_keys=True), now, now),
            )

    def update_run(self, **values: object) -> None:
        allowed = {
            "state",
            "merge_state",
            "merge_worker_id",
            "output_path",
            "error",
            "cancel_requested",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"unknown run fields: {', '.join(sorted(unknown))}")
        if not values:
            return
        values["updated_at"] = int(time.time())
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connection() as db:
            db.execute(
                f"UPDATE runs SET {assignments} WHERE id=1",
                tuple(values.values()),
            )

    def add_jobs(self, jobs: list[dict[str, str]]) -> None:
        with self.connection() as db:
            db.executemany(
                """INSERT INTO jobs (xhtml_path, locator, input_path)
                   VALUES (?, ?, ?)""",
                [(job["xhtml_path"], job["locator"], job["input_path"]) for job in jobs],
            )

    def config(self) -> dict[str, object]:
        return self._config()

    def claim(self, worker_id: str, now: int | None = None) -> ClaimedJob | None:
        now = int(time.time()) if now is None else now
        token = uuid.uuid4().hex
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT cancel_requested, config_json FROM runs WHERE id=1"
            ).fetchone()
            if run is None:
                raise ValueError("run has not been created")
            if run["cancel_requested"]:
                return None
            config = json.loads(run["config_json"])
            max_attempts = int(config.get("max_attempts", 3))
            row = db.execute(
                """SELECT * FROM jobs
                   WHERE (state='pending'
                      OR (state='leased' AND lease_until < ?)
                      OR (state='failed' AND attempts < ?))
                   ORDER BY id LIMIT 1""",
                (now, max_attempts),
            ).fetchone()
            if row is None:
                return None
            changed = db.execute(
                """UPDATE jobs
                   SET state='leased', worker_id=?, lease_token=?, lease_until=?,
                       attempts=attempts+1, error=NULL
                   WHERE id=? AND
                         (state='pending'
                          OR (state='leased' AND lease_until < ?)
                          OR (state='failed' AND attempts < ?))""",
                (worker_id, token, now + LEASE_SECONDS, row["id"], now, max_attempts),
            ).rowcount
            if changed != 1:
                return None
            claimed = db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()
            return ClaimedJob.from_row(claimed, config)

    def heartbeat(
        self,
        job_id: int,
        worker_id: str,
        lease_token: str,
        now: int | None = None,
    ) -> bool:
        now = int(time.time()) if now is None else now
        with self.connection() as db:
            return (
                db.execute(
                    """UPDATE jobs SET lease_until=?
                       WHERE id=? AND state='leased' AND worker_id=?
                         AND lease_token=? AND lease_until >= ?""",
                    (now + LEASE_SECONDS, job_id, worker_id, lease_token, now),
                ).rowcount
                == 1
            )

    def complete(
        self,
        job_id: int,
        worker_id: str,
        lease_token: str,
        result_path: str,
    ) -> bool:
        with self.connection() as db:
            changed = db.execute(
                """UPDATE jobs SET state='done', result_path=?, worker_id=NULL,
                       lease_token=NULL, lease_until=NULL, error=NULL
                   WHERE id=? AND state='leased' AND worker_id=? AND lease_token=?""",
                (result_path, job_id, worker_id, lease_token),
            ).rowcount
            if changed:
                db.execute("UPDATE runs SET updated_at=? WHERE id=1", (int(time.time()),))
            return changed == 1

    def fail(
        self,
        job_id: int,
        worker_id: str,
        lease_token: str,
        error: str,
    ) -> bool:
        with self.connection() as db:
            changed = db.execute(
                """UPDATE jobs SET state='failed', error=?, worker_id=NULL,
                       lease_token=NULL, lease_until=NULL
                   WHERE id=? AND state='leased' AND worker_id=? AND lease_token=?""",
                (error[:4000], job_id, worker_id, lease_token),
            ).rowcount
            if changed:
                db.execute("UPDATE runs SET updated_at=? WHERE id=1", (int(time.time()),))
            return changed == 1

    def request_cancel(self) -> bool:
        with self.connection() as db:
            return db.execute(
                "UPDATE runs SET cancel_requested=1, updated_at=? WHERE id=1",
                (int(time.time()),),
            ).rowcount == 1

    def clear_cancel(self) -> bool:
        with self.connection() as db:
            return db.execute(
                "UPDATE runs SET cancel_requested=0, state='running', error=NULL, updated_at=? WHERE id=1",
                (int(time.time()),),
            ).rowcount == 1

    def retry_failed(self, job_ids: list[int] | None = None) -> int:
        with self.connection() as db:
            if job_ids:
                marks = ",".join("?" for _ in job_ids)
                cursor = db.execute(
                    f"""UPDATE jobs SET state='pending', attempts=0, error=NULL
                        WHERE state='failed' AND id IN ({marks})""",
                    tuple(job_ids),
                )
            else:
                cursor = db.execute(
                    "UPDATE jobs SET state='pending', attempts=0, error=NULL WHERE state='failed'"
                )
            db.execute(
                """UPDATE runs SET cancel_requested=0, state='running', error=NULL, updated_at=?
                   WHERE id=1""",
                (int(time.time()),),
            )
            return cursor.rowcount

    def try_claim_merge(self, worker_id: str) -> bool:
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT cancel_requested, merge_state FROM runs WHERE id=1"
            ).fetchone()
            if run is None or run["cancel_requested"] or run["merge_state"] != "pending":
                return False
            unfinished = db.execute(
                "SELECT 1 FROM jobs WHERE state != 'done' LIMIT 1"
            ).fetchone()
            if unfinished is not None:
                return False
            return (
                db.execute(
                    """UPDATE runs SET merge_state='leased', merge_worker_id=?, updated_at=?
                       WHERE id=1 AND merge_state='pending'""",
                    (worker_id, int(time.time())),
                ).rowcount
                == 1
            )

    def summary(self) -> dict[str, object]:
        with self.connection() as db:
            run = db.execute("SELECT * FROM runs WHERE id=1").fetchone()
            if run is None:
                raise ValueError("run has not been created")
            counts = {
                row["state"]: row["count"]
                for row in db.execute("SELECT state, COUNT(*) AS count FROM jobs GROUP BY state")
            }
            total = sum(counts.values())
            return {
                "state": run["state"],
                "mergeState": run["merge_state"],
                "total": total,
                "done": counts.get("done", 0),
                "leased": counts.get("leased", 0),
                "failed": counts.get("failed", 0),
                "pending": counts.get("pending", 0),
                "startedAt": run["started_at"],
                "cancelRequested": bool(run["cancel_requested"]),
                "outputPath": run["output_path"],
                "error": run["error"],
            }


# The CLI is filled in by the EPUB preparation task. Keeping a useful parser here
# makes accidental direct invocation fail with a clear message rather than import
# side effects.
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", help="run-store command")
    parser.parse_args(argv)
    parser.error("run-store command is not implemented yet")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
