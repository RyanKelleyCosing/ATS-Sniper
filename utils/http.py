"""HTTP helpers with retry logic for ATS Sniper."""

import asyncio
import time
import logging
from typing import Any

import httpx
import requests

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def requests_get_with_retry(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: int = 15,
    max_retries: int = _MAX_RETRIES,
) -> requests.Response:
    """GET request with exponential backoff on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(
                url, headers=headers, params=params, timeout=timeout
            )
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "Retryable status %d from %s, waiting %.1fs (attempt %d/%d)",
                    response.status_code, url, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "%s on %s, retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__, url, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            else:
                raise
    raise last_exc  # type: ignore[misc]


def requests_post_with_retry(
    url: str,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    timeout: int = 15,
    max_retries: int = _MAX_RETRIES,
) -> requests.Response:
    """POST request with exponential backoff on transient failures."""
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                url, json=json, headers=headers, timeout=timeout
            )
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "Retryable status %d from %s, waiting %.1fs (attempt %d/%d)",
                    response.status_code, url, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait = _BACKOFF_BASE ** attempt
                logger.warning(
                    "%s on %s, retrying in %.1fs (attempt %d/%d)",
                    type(exc).__name__, url, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
            else:
                raise
    raise last_exc  # type: ignore[misc]


async def httpx_get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
    max_retries: int = _MAX_RETRIES,
    retry_label: str = "HTTP request",
) -> httpx.Response:
    """Async GET request with exponential backoff on transient request failures."""
    attempts = max(int(max_retries or 0), 1)
    last_exc: httpx.RequestError | None = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, headers=headers, timeout=timeout)
            if response.status_code in _RETRYABLE_STATUS_CODES and attempt < attempts:
                wait = _BACKOFF_BASE ** (attempt - 1)
                logger.warning(
                    "%s returned retryable status %d from %s, waiting %.1fs (attempt %d/%d)",
                    retry_label,
                    response.status_code,
                    url,
                    wait,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(wait)
                continue
            return response
        except httpx.RequestError as exc:
            last_exc = exc
            if attempt < attempts:
                wait = _BACKOFF_BASE ** (attempt - 1)
                logger.warning(
                    "%s on %s, retrying in %.1fs (attempt %d/%d): %s",
                    type(exc).__name__,
                    url,
                    wait,
                    attempt,
                    attempts,
                    retry_label,
                )
                await asyncio.sleep(wait)
                continue
            raise

    raise last_exc  # type: ignore[misc]
