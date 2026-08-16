import pytest

from src.web import create_app
from src.web.limiter import limiter


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    limiter.reset()
    with app.test_client() as c:
        yield c
    limiter.reset()


class TestCors:
    def test_no_cors_headers(self, client):
        """The frontend is same-origin; no route should hand out CORS headers."""
        res = client.get("/api/album-types")
        assert res.status_code == 200
        assert "Access-Control-Allow-Origin" not in res.headers


class TestRateLimit:
    def test_default_limit_returns_429_json(self, client):
        """The default 200/hour applies to routes without their own limit."""
        last = None
        for _ in range(201):
            last = client.get("/api/album-types")

        assert last.status_code == 429
        assert "Too many requests" in last.get_json()["error"]

    def test_search_limit_is_stricter_than_default(self, client):
        """/api/search caps at 20/minute, well before the 200/hour default."""
        statuses = [client.post("/api/search", json={}).status_code for _ in range(21)]

        assert statuses[-1] == 429
        assert 429 not in statuses[:20]

    def test_create_limit_is_stricter_than_search(self, client):
        """/api/create caps at 5/minute — it writes to the owner's account."""
        statuses = [client.post("/api/create", json={}).status_code for _ in range(6)]

        assert statuses[-1] == 429
        assert 429 not in statuses[:5]

    def test_limits_are_keyed_by_remote_address(self, client):
        for _ in range(5):
            client.post("/api/create", json={}, environ_overrides={"REMOTE_ADDR": "10.0.0.1"})

        blocked = client.post(
            "/api/create", json={}, environ_overrides={"REMOTE_ADDR": "10.0.0.1"}
        )
        other = client.post(
            "/api/create", json={}, environ_overrides={"REMOTE_ADDR": "10.0.0.2"}
        )

        assert blocked.status_code == 429
        assert other.status_code != 429

    def test_proxy_forwarded_for_keys_the_limit(self, client):
        """ProxyFix must make X-Forwarded-For the key behind Railway's proxy."""
        for _ in range(5):
            client.post("/api/create", json={}, headers={"X-Forwarded-For": "203.0.113.5"})

        blocked = client.post(
            "/api/create", json={}, headers={"X-Forwarded-For": "203.0.113.5"}
        )
        other = client.post(
            "/api/create", json={}, headers={"X-Forwarded-For": "203.0.113.9"}
        )

        assert blocked.status_code == 429
        assert other.status_code != 429
