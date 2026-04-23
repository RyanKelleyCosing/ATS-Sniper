"""Unit tests for Greenhouse scraper request resilience."""

import asyncio
from pathlib import Path
import sys

import httpx


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

import greenhouse_scraper  # noqa: E402


class FakeGreenhouseResponse:
    """Minimal httpx-compatible response for Greenhouse scraper tests."""

    def __init__(self, status_code: int, payload, url: str) -> None:
        self.status_code = status_code
        self._payload = payload
        self.request = httpx.Request("GET", url)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("HTTP error", request=self.request, response=response)

    def json(self):
        return self._payload


def test_fetch_greenhouse_jobs_retries_request_issue_before_success(monkeypatch) -> None:
    call_counter = {"count": 0}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None, timeout=None):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise httpx.ConnectError("dns failed", request=httpx.Request("GET", url))
            return FakeGreenhouseResponse(200, {"jobs": [{"id": "job-123"}]}, url)

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(greenhouse_scraper.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(greenhouse_scraper.asyncio, "sleep", fake_sleep)

    jobs = asyncio.run(greenhouse_scraper.fetch_greenhouse_jobs("gitlab"))

    assert call_counter["count"] == 2
    assert jobs == [{"id": "job-123"}]