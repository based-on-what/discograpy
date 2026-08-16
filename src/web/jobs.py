"""In-memory background job store for long-running requests.

Building a playlist for a large discography takes minutes: it holds a gunicorn
worker and times out behind proxies. /api/create submits the work here and
returns a job id the client polls.

Stdlib only, single process on purpose. Jobs live in this process's memory, so
gunicorn MUST run with ``--workers 1 --threads N``: with several workers a
client would poll a worker that never saw its own job. See CLAUDE.md.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Optional

# Finished jobs stay readable for an hour, then get purged so the dict does not
# grow without bound in a long-lived process.
JOB_TTL_SECONDS = 3600

MAX_WORKERS = 4

_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="discograpy-job")
_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


class JobError(Exception):
    """A failure the client should see verbatim, with its original HTTP status.

    Lets the worker keep the error->HTTP mapping the synchronous route had.
    """

    def __init__(self, message: str, status_code: int = 400, **extra: Any) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.extra = extra


def submit(work: Callable[[], Dict[str, Any]]) -> str:
    """Queue ``work`` and return the job id to poll."""
    job_id = uuid.uuid4().hex
    with _lock:
        _purge_expired_locked()
        _jobs[job_id] = {"status": "pending", "created_at": time.monotonic()}
    _executor.submit(_run, job_id, work)
    return job_id


def get(job_id: str) -> Optional[Dict[str, Any]]:
    """Return the job's public payload, or None if unknown or expired."""
    with _lock:
        _purge_expired_locked()
        job = _jobs.get(job_id)
        if job is None:
            return None

        if job["status"] == "pending":
            return {"status": "pending"}
        if job["status"] == "done":
            return {"status": "done", "result": job["result"]}
        return {
            "status": "error",
            "error": job["error"],
            "status_code": job["status_code"],
            **job["extra"],
        }


def _run(job_id: str, work: Callable[[], Dict[str, Any]]) -> None:
    try:
        result = work()
        record: Dict[str, Any] = {"status": "done", "result": result}
    except JobError as exc:
        record = {
            "status": "error",
            "error": exc.message,
            "status_code": exc.status_code,
            "extra": exc.extra,
        }
    except Exception as exc:  # noqa: BLE001 - the job must never die silently
        record = {
            "status": "error",
            "error": f"Internal server error: {exc}",
            "status_code": 500,
            "extra": {},
        }

    with _lock:
        # The job may have been purged if the client never polled.
        if job_id in _jobs:
            _jobs[job_id].update(record, finished_at=time.monotonic())


def _purge_expired_locked() -> None:
    """Drop finished jobs older than the TTL. Caller must hold ``_lock``."""
    now = time.monotonic()
    expired = [
        job_id
        for job_id, job in _jobs.items()
        if job.get("finished_at") is not None and now - job["finished_at"] > JOB_TTL_SECONDS
    ]
    for job_id in expired:
        del _jobs[job_id]


def _reset_for_tests() -> None:
    with _lock:
        _jobs.clear()
