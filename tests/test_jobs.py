import time

import pytest

from src.web import jobs


@pytest.fixture(autouse=True)
def clean_store():
    jobs._reset_for_tests()
    yield
    jobs._reset_for_tests()


def wait_until_settled(job_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = jobs.get(job_id)
        if payload["status"] != "pending":
            return payload
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} still pending after {timeout}s")


class TestSubmit:
    def test_unknown_job_is_none(self):
        assert jobs.get("nope") is None

    def test_result_is_returned_on_success(self):
        job_id = jobs.submit(lambda: {"tracks_added": 42})

        assert wait_until_settled(job_id) == {"status": "done", "result": {"tracks_added": 42}}

    def test_pending_before_the_work_finishes(self):
        release = __import__("threading").Event()
        job_id = jobs.submit(lambda: (release.wait(5), {"ok": True})[1])
        try:
            assert jobs.get(job_id) == {"status": "pending"}
        finally:
            release.set()
        wait_until_settled(job_id)

    def test_ids_are_unique(self):
        ids = {jobs.submit(lambda: {}) for _ in range(20)}

        assert len(ids) == 20


class TestErrors:
    def test_job_error_keeps_status_code_and_extras(self):
        def work():
            raise jobs.JobError("nope", 401, auth_required=True)

        payload = wait_until_settled(jobs.submit(work))

        assert payload == {
            "status": "error",
            "error": "nope",
            "status_code": 401,
            "auth_required": True,
        }

    def test_unexpected_exception_becomes_a_generic_500(self, caplog):
        def work():
            raise RuntimeError("boom at /srv/app/secret-path")

        payload = wait_until_settled(jobs.submit(work))

        assert payload == {
            "status": "error",
            "error": "Internal server error",
            "status_code": 500,
        }
        assert "boom at /srv/app/secret-path" in caplog.text

    def test_a_failing_job_does_not_kill_the_executor(self):
        wait_until_settled(jobs.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
        job_id = jobs.submit(lambda: {"ok": True})

        assert wait_until_settled(job_id)["result"] == {"ok": True}


class TestExpiry:
    def test_finished_jobs_are_purged_after_the_ttl(self, monkeypatch):
        job_id = jobs.submit(lambda: {"ok": True})
        wait_until_settled(job_id)

        monkeypatch.setattr(jobs, "JOB_TTL_SECONDS", -1)

        assert jobs.get(job_id) is None

    def test_pending_jobs_are_never_purged(self, monkeypatch):
        release = __import__("threading").Event()
        job_id = jobs.submit(lambda: (release.wait(5), {"ok": True})[1])
        monkeypatch.setattr(jobs, "JOB_TTL_SECONDS", -1)
        try:
            # The TTL only counts from finished_at, so an in-flight job survives
            # a purge no matter how long it has been running.
            assert jobs.get(job_id) == {"status": "pending"}
        finally:
            release.set()
