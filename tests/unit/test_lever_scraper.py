"""Unit tests for Lever scraper request resilience."""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import sys

import httpx


ATS_ROOT = Path(__file__).resolve().parents[2]
if str(ATS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATS_ROOT))

import lever_scraper  # noqa: E402


class FakeLeverResponse:
    """Minimal httpx-compatible response for Lever scraper tests."""

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


def test_fetch_lever_jobs_retries_request_issue_before_success(monkeypatch, capsys) -> None:
    call_counter = {"count": 0}

    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None):
            call_counter["count"] += 1
            if call_counter["count"] == 1:
                raise httpx.ReadTimeout("timed out", request=httpx.Request("GET", url))
            return FakeLeverResponse(200, [{"id": "job-123"}], url)

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(lever_scraper.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(lever_scraper.asyncio, "sleep", fake_sleep)

    jobs = asyncio.run(lever_scraper.fetch_lever_jobs("restaurant365"))
    captured = capsys.readouterr().out.lower()

    assert call_counter["count"] == 2
    assert jobs == [{"id": "job-123"}]
    assert "retrying" in captured


def test_fetch_lever_jobs_logs_board_unavailable_without_failure_label(monkeypatch, capsys) -> None:
    class FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers=None):
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.",
                request=httpx.Request("GET", url),
            )

    async def fake_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(lever_scraper.httpx, "AsyncClient", lambda *args, **kwargs: FakeAsyncClient())
    monkeypatch.setattr(lever_scraper.asyncio, "sleep", fake_sleep)

    jobs = asyncio.run(lever_scraper.fetch_lever_jobs("restaurant365"))
    captured = capsys.readouterr().out.lower()

    assert jobs is None
    assert "lever board unavailable for 'restaurant365'" in captured
    assert "lever request error" not in captured


def test_update_lever_board_health_disables_repeated_dead_endpoints() -> None:
    state = {"board_health": {}}
    settings = {
        "enabled": True,
        "downgrade_after_failures": 2,
        "disable_after_failures": 3,
        "disable_hours": 24,
    }

    lever_scraper.update_lever_board_health(
        state,
        "h1insights",
        success=False,
        failure_kind="not_found",
        failure_message="Company not found (404)",
        settings=settings,
    )
    lever_scraper.update_lever_board_health(
        state,
        "h1insights",
        success=False,
        failure_kind="not_found",
        failure_message="Company not found (404)",
        settings=settings,
    )
    record = lever_scraper.update_lever_board_health(
        state,
        "h1insights",
        success=False,
        failure_kind="not_found",
        failure_message="Company not found (404)",
        settings=settings,
    )

    assert record["last_status"] == "disabled"
    assert lever_scraper.is_lever_board_disabled(record) is True


def test_run_lever_scrape_skips_temporarily_disabled_board(monkeypatch, capsys) -> None:
    disabled_until = (datetime.now() + timedelta(hours=6)).isoformat()

    monkeypatch.setattr(lever_scraper, "load_config", lambda: {"settings": {}})
    monkeypatch.setattr(
        lever_scraper,
        "load_state",
        lambda: {
            "seen_jobs": {},
            "board_health": {
                "lever": {
                    "h1insights": {
                        "consecutive_failures": 3,
                        "last_status": "disabled",
                        "disabled_until": disabled_until,
                    }
                }
            },
        },
    )
    monkeypatch.setattr(
        lever_scraper,
        "save_state",
        lambda _state: (_ for _ in ()).throw(AssertionError("dry run should not save state")),
    )

    jobs = asyncio.run(
        lever_scraper.run_lever_scrape(
            dry_run=True,
            endpoints={"h1": {"name": "H1", "company_slug": "h1insights", "priority": "MEDIUM"}},
        )
    )
    captured = capsys.readouterr().out.lower()

    assert jobs == []
    assert "skipped; board disabled until" in captured