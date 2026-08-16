from app.config import Config
from app.rate_limit import rate_limiter


def test_rate_limit_blocks_after_max_requests(client):
    original_max = rate_limiter.max_requests
    rate_limiter.max_requests = 3
    try:
        for _ in range(3):
            resp = client.post("/api/v1/shorten", json={"url": "https://example.com"})
            assert resp.status_code == 201
        blocked = client.post("/api/v1/shorten", json={"url": "https://example.com"})
        assert blocked.status_code == 429
        body = blocked.get_json()
        assert body["error"] == "RateLimitError"
        assert body["rate_limit"]["remaining"] == 0
    finally:
        rate_limiter.max_requests = original_max


def test_health_endpoint_is_not_rate_limited(client):
    for _ in range(50):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
