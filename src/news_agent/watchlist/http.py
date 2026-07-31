from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from email.message import Message
from typing import Any, Callable, Protocol


class HttpResponse(Protocol):
    status: int
    headers: Message

    def read(self) -> bytes: ...


OpenUrl = Callable[[urllib.request.Request, float], HttpResponse]


@dataclass(frozen=True)
class JsonResponse:
    status: int
    data: dict[str, Any] | None
    headers: dict[str, str]
    attempts: int
    error_code: str = ""


@dataclass(frozen=True)
class BytesResponse:
    status: int
    data: bytes | None
    headers: dict[str, str]
    attempts: int
    error_code: str = ""


class RetryingJsonClient:
    def __init__(
        self,
        *,
        opener: OpenUrl | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        timeout_seconds: float = 15.0,
        max_attempts: int = 3,
    ) -> None:
        self._opener = opener or _open
        self._sleep = sleep
        self._jitter = jitter
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    def get(self, url: str, headers: dict[str, str]) -> JsonResponse:
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, headers=headers)
            try:
                response = self._opener(request, self.timeout_seconds)
                response_headers = {key.casefold(): value for key, value in response.headers.items()}
                if response.status == 304:
                    return JsonResponse(304, None, response_headers, attempt)
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    return JsonResponse(response.status, None, response_headers, attempt, "invalid_json_shape")
                return JsonResponse(response.status, payload, response_headers, attempt)
            except urllib.error.HTTPError as exc:
                if exc.code == 304:
                    return JsonResponse(304, None, _headers(exc.headers), attempt)
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    return JsonResponse(exc.code, None, _headers(exc.headers), attempt, f"http_{exc.code}")
                if attempt == self.max_attempts:
                    return JsonResponse(exc.code, None, _headers(exc.headers), attempt, f"http_{exc.code}")
                self._sleep(_delay(attempt, exc.headers.get("Retry-After"), self._jitter()))
            except (TimeoutError, urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
                if attempt == self.max_attempts:
                    return JsonResponse(0, None, {}, attempt, _network_error_code(exc))
                self._sleep(_delay(attempt, None, self._jitter()))
        raise AssertionError("retry loop exhausted unexpectedly")


class RetryingBytesClient:
    def __init__(
        self,
        *,
        opener: OpenUrl | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
        timeout_seconds: float = 15.0,
        max_attempts: int = 3,
    ) -> None:
        self._opener = opener or _open
        self._sleep = sleep
        self._jitter = jitter
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts

    def get(self, url: str, headers: dict[str, str]) -> BytesResponse:
        for attempt in range(1, self.max_attempts + 1):
            request = urllib.request.Request(url, headers=headers)
            try:
                response = self._opener(request, self.timeout_seconds)
                response_headers = {key.casefold(): value for key, value in response.headers.items()}
                return BytesResponse(response.status, response.read(), response_headers, attempt)
            except urllib.error.HTTPError as exc:
                if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                    return BytesResponse(exc.code, None, _headers(exc.headers), attempt, f"http_{exc.code}")
                if attempt == self.max_attempts:
                    return BytesResponse(exc.code, None, _headers(exc.headers), attempt, f"http_{exc.code}")
                self._sleep(_delay(attempt, exc.headers.get("Retry-After"), self._jitter()))
            except (TimeoutError, urllib.error.URLError, OSError) as exc:
                if attempt == self.max_attempts:
                    return BytesResponse(0, None, {}, attempt, _network_error_code(exc))
                self._sleep(_delay(attempt, None, self._jitter()))
        raise AssertionError("retry loop exhausted unexpectedly")


def _open(request: urllib.request.Request, timeout: float) -> HttpResponse:
    return urllib.request.urlopen(request, timeout=timeout)  # type: ignore[return-value]


def _headers(headers: Message | None) -> dict[str, str]:
    return {key.casefold(): value for key, value in headers.items()} if headers else {}


def _delay(attempt: int, retry_after: str | None, jitter: float) -> float:
    if retry_after:
        try:
            return min(30.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return min(8.0, 2 ** (attempt - 1) + max(0.0, min(1.0, jitter)))


def _network_error_code(exc: BaseException) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_json"
    if isinstance(exc, TimeoutError):
        return "timeout"
    return "network_error"
