"""Durable application jobs shared by the API and background workers."""
import json
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from backend.store import utc_now


JOB_KINDS = frozenset({"benchmark", "dataset_build", "dataset_answers", "ai_report"})
LEASE_SECONDS = 60


def durable_tasks_enabled() -> bool:
    return os.getenv("PRISMLAB_DURABLE_TASKS", "false").lower() in {"1", "true", "yes", "on"}


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class TaskQueue:
    """PostgreSQL-backed queue scoped to one workspace schema."""

    def __init__(self, store):
        self.store = store

    def enqueue(self, kind: str, payload: dict, *, job_id: str | None = None) -> dict:
        if kind not in JOB_KINDS:
            raise ValueError(f"Unsupported background job kind: {kind}")
        identifier = job_id or uuid.uuid4().hex
        now = utc_now()
        with self.store._session() as connection:
            row = connection.execute(
                """INSERT INTO background_jobs
                   (id,created_at,updated_at,kind,status,payload,attempt,lease_owner,lease_until,error)
                   VALUES (%s,%s,%s,%s,'queued',%s,'0',NULL,NULL,NULL)
                   ON CONFLICT(id) DO NOTHING
                   RETURNING id,created_at,updated_at,kind,status,payload,attempt,lease_owner,lease_until,error""",
                (identifier, now, now, kind, encoded(payload)),
            ).fetchone()
            if row is None:
                row = connection.execute("SELECT * FROM background_jobs WHERE id=%s", (identifier,)).fetchone()
        return self._decode(row)

    def claim(self, owner: str, kinds: set[str] | frozenset[str] = JOB_KINDS) -> dict | None:
        kinds = set(kinds)
        if not kinds:
            return None
        with self.store._session() as connection:
            row = connection.execute(
                """SELECT * FROM background_jobs
                   WHERE kind=ANY(%s) AND
                     (status='queued' OR
                      (status='running' AND lease_until IS NOT NULL AND lease_until::timestamptz < CURRENT_TIMESTAMP))
                   ORDER BY created_at,id
                   FOR UPDATE SKIP LOCKED LIMIT 1""",
                (list(kinds),),
            ).fetchone()
            if row is None:
                return None
            now = datetime.now(timezone.utc)
            attempt = int(row["attempt"] or 0) + 1
            lease_until = (now + timedelta(seconds=LEASE_SECONDS)).isoformat()
            connection.execute(
                """UPDATE background_jobs
                   SET updated_at=%s,status='running',attempt=%s,lease_owner=%s,lease_until=%s,error=NULL
                   WHERE id=%s""",
                (utc_now(), str(attempt), owner, lease_until, row["id"]),
            )
            row = {**dict(row), "updated_at": utc_now(), "status": "running",
                   "attempt": str(attempt), "lease_owner": owner, "lease_until": lease_until, "error": None}
        return self._decode(row)

    def heartbeat(self, job_id: str, owner: str) -> None:
        lease_until = (datetime.now(timezone.utc) + timedelta(seconds=LEASE_SECONDS)).isoformat()
        with self.store._session() as connection:
            updated = connection.execute(
                """UPDATE background_jobs SET updated_at=%s,lease_until=%s
                   WHERE id=%s AND status='running' AND lease_owner=%s""",
                (utc_now(), lease_until, job_id, owner),
            ).rowcount
        if not updated:
            raise HTTPException(409, "Background job lease is no longer valid")

    def complete(self, job_id: str, owner: str) -> None:
        self._settle(job_id, owner, "completed", None)

    def fail(self, job_id: str, owner: str, error: str) -> None:
        self._settle(job_id, owner, "failed", error[:1000])

    def cancel(self, job_id: str) -> bool:
        with self.store._session() as connection:
            updated = connection.execute(
                """UPDATE background_jobs SET updated_at=%s,status='cancelled',lease_owner=NULL,lease_until=NULL
                   WHERE id=%s AND status IN ('queued','running')""",
                (utc_now(), job_id),
            ).rowcount
        return bool(updated)

    def get(self, job_id: str) -> dict | None:
        with self.store._session() as connection:
            row = connection.execute("SELECT * FROM background_jobs WHERE id=%s", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def _settle(self, job_id: str, owner: str, status: str, error: str | None) -> None:
        with self.store._session() as connection:
            updated = connection.execute(
                """UPDATE background_jobs SET updated_at=%s,status=%s,lease_owner=NULL,lease_until=NULL,error=%s
                   WHERE id=%s AND status='running' AND lease_owner=%s""",
                (utc_now(), status, error, job_id, owner),
            ).rowcount
        if not updated:
            raise HTTPException(409, "Background job lease is no longer valid")

    @staticmethod
    def _decode(row) -> dict:
        if row is None:
            return {}
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        value["attempt"] = int(value.get("attempt") or 0)
        return value
