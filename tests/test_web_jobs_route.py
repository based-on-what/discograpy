import pytest

from src.web import create_app, jobs
from src.web.limiter import limiter


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    limiter.reset()
    jobs._reset_for_tests()
    with app.test_client() as c:
        yield c
    limiter.reset()
    jobs._reset_for_tests()


class TestJobStatusRoute:
    def test_unknown_job_is_404(self, client):
        res = client.get("/api/jobs/does-not-exist")

        assert res.status_code == 404
        assert res.get_json() == {"error": "Unknown or expired job"}

    def test_pending_job(self, client):
        import threading

        release = threading.Event()
        job_id = jobs.submit(lambda: (release.wait(5), {"ok": True})[1])
        try:
            res = client.get(f"/api/jobs/{job_id}")
            assert res.status_code == 200
            assert res.get_json() == {"status": "pending"}
        finally:
            release.set()

    def test_done_job_carries_the_result(self, client):
        job_id = jobs.submit(lambda: {"tracks_added": 7})
        _settle(job_id)

        res = client.get(f"/api/jobs/{job_id}")

        assert res.status_code == 200
        assert res.get_json() == {"status": "done", "result": {"tracks_added": 7}}

    def test_error_job_carries_status_code_and_auth_flag(self, client):
        def work():
            raise jobs.JobError("needs auth", 401, auth_required=True)

        job_id = jobs.submit(work)
        _settle(job_id)

        # 200 on the poll itself; the original status travels in the body so the
        # frontend can keep its 401/auth_required handling.
        res = client.get(f"/api/jobs/{job_id}")

        assert res.status_code == 200
        assert res.get_json() == {
            "status": "error",
            "error": "needs auth",
            "status_code": 401,
            "auth_required": True,
        }


class TestCreateAccepts:
    def test_validation_still_rejects_before_queueing(self, client):
        res = client.post("/api/create", json={})

        assert res.status_code == 400
        assert "required" in res.get_json()["error"]
        assert jobs.get("") is None

    def test_valid_request_returns_202_and_the_job_completes(self, client, monkeypatch):
        import src.web as web
        from src.domain.models import RunSummary

        summary = RunSummary(
            artist="Radiohead",
            playlist_name="Radiohead discography",
            playlist_id="pl123",
            albums_included=9,
            tracks_added=120,
            total_ms=3_600_000,
            cover_applied=False,
            cover_error=None,
            dry_run=False,
        )

        class FakeService:
            def build_playlist(self, **kwargs):
                return summary

        monkeypatch.setattr(web, "get_client", lambda: object())
        monkeypatch.setattr(web, "get_service", lambda: FakeService())
        monkeypatch.setattr("src.web.routes._ensure_spotify_token", lambda client: None)

        res = client.post(
            "/api/create",
            json={"artist_id": "abc", "artist_name": "Radiohead", "album_type_selection": 0},
        )

        assert res.status_code == 202
        job_id = res.get_json()["job_id"]

        _settle(job_id)
        result = client.get(f"/api/jobs/{job_id}").get_json()["result"]

        assert result["playlist_id"] == "pl123"
        assert result["playlist_url"] == "https://open.spotify.com/playlist/pl123"
        assert result["tracks_added"] == 120
        assert result["total_length"] == "1h 0m 0s"

    def test_missing_env_message_survives_into_the_job(self, client, monkeypatch):
        """The client must be built inside the job, not in the request thread.

        Built outside, EnvironmentError escapes to the catch-all handler and the
        caller gets a bare "Internal server error" instead of the missing vars.
        """
        import src.web as web

        def boom():
            raise EnvironmentError("Missing required environment variables: SPOTIPY_CLIENT_ID")

        monkeypatch.setattr(web, "get_client", boom)

        res = client.post(
            "/api/create",
            json={"artist_id": "abc", "artist_name": "X", "album_type_selection": 0},
        )
        assert res.status_code == 202

        job_id = res.get_json()["job_id"]
        _settle(job_id)
        payload = client.get(f"/api/jobs/{job_id}").get_json()

        assert payload["status_code"] == 500
        assert "SPOTIPY_CLIENT_ID" in payload["error"]

    def test_bad_album_type_is_rejected(self, client):
        res = client.post(
            "/api/create",
            json={"artist_id": "abc", "artist_name": "X", "album_type_selection": 99},
        )

        assert res.status_code == 400
        assert "album_type_selection" in res.get_json()["error"]


def _settle(job_id, timeout=5.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if jobs.get(job_id)["status"] != "pending":
            return
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} still pending after {timeout}s")
