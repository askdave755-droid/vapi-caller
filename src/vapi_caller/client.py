"""Vapi REST client. Endpoints follow the current docs at https://docs.vapi.ai.

POST https://api.vapi.ai/call              Create / outbound call
GET  https://api.vapi.ai/call/{id}         Get call (poll for status)
GET  https://api.vapi.ai/call              List calls
GET  https://api.vapi.ai/assistant         List assistants (read-only)
GET  https://api.vapi.ai/phone-number      List phone numbers (read-only)
POST {call.monitor.controlUrl}            End a live call with {"type": "end-call"}

This module never prints VAPI_API_KEY. Messages and error text redact secrets.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from vapi_caller.results import _now_iso

API_BASE = "https://api.vapi.ai"
TERMINAL_STATUSES = frozenset({"ended", "not-found", "deletion-failed"})
DEFAULT_POLL_INTERVAL = 2.0
DEFAULT_TIMEOUT = 900.0
REDACTED = "***REDACTED***"


class VapiError(Exception):
    """User-facing API or usage error. Messages must never include secrets."""


def _redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in (headers or {}).items():
        if key.lower() == "authorization":
            redacted[key] = REDACTED
        else:
            redacted[key] = value
    return redacted


def _safe_url(url: str) -> str:
    """Return a URL with any userinfo or api-key-like query values stripped."""
    parsed = urllib.parse.urlparse(url)
    if parsed.query:
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        safe_query = [
            (k, REDACTED if "key" in k.lower() or "token" in k.lower() or "secret" in k.lower() else v)
            for k, v in query
        ]
        parsed = parsed._replace(query=urllib.parse.urlencode(safe_query), netloc=parsed.hostname or "")
    elif parsed.username or parsed.password:
        parsed = parsed._replace(netloc=parsed.hostname or "")
    return urllib.parse.urlunparse(parsed)


def _truncate(text: str, limit: int = 500) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def default_http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        payload = exc.read() if exc.fp else b""
        raise VapiError(
            f"HTTP {exc.code} {method} {_safe_url(url)}: {_truncate(payload.decode('utf-8', 'replace'))}"
        ) from None
    except urllib.error.URLError as exc:
        raise VapiError(f"Network error {method} {_safe_url(url)}: {exc.reason}") from None


class VapiClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = API_BASE,
        http: Callable[..., tuple[int, dict[str, str], bytes]] = default_http,
        public_api_key: str | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = http
        self.public_api_key = public_api_key
        self.sleeper = sleeper
        self.clock = clock

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            raise VapiError("VAPI_API_KEY is not set. Export it, add it to a .env file, or pass --api-key-env.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any | None = None,
        query: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        authenticate: bool = True,
        timeout: float = 30.0,
    ) -> Any:
        url = path if path.startswith("http://") or path.startswith("https://") else f"{self.base_url}{path}"
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                url = f"{url}?{urllib.parse.urlencode(filtered)}"
        request_headers = dict(headers or {})
        if authenticate:
            request_headers.update(self._auth_headers())
        elif "Content-Type" not in {k.title() for k in request_headers}:
            request_headers.setdefault("Content-Type", "application/json")
        payload = None if body is None else json.dumps(body).encode("utf-8")
        status, _response_headers, raw = self.http(
            method, url, headers=request_headers, body=payload, timeout=timeout
        )
        if raw:
            try:
                return json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return raw.decode("utf-8", "replace")
        return None

    def create_call(self, payload: dict[str, Any]) -> Any:
        return self.request("POST", "/call", body=payload)

    def get_call(self, call_id: str) -> Any:
        return self.request("GET", f"/call/{urllib.parse.quote(call_id, safe='')}")

    def list_calls(
        self,
        *,
        limit: int = 20,
        assistant_id: str | None = None,
        phone_number_id: str | None = None,
    ) -> Any:
        return self.request(
            "GET",
            "/call",
            query={"limit": limit, "assistantId": assistant_id, "phoneNumberId": phone_number_id},
        )

    def list_assistants(self, *, limit: int = 20) -> Any:
        return self.request("GET", "/assistant", query={"limit": limit})

    def list_phone_numbers(self, *, limit: int = 20) -> Any:
        return self.request("GET", "/phone-number", query={"limit": limit})

    def end_call(self, call_id: str) -> dict[str, Any]:
        """End a live call via POST to call.monitor.controlUrl with {"type": "end-call"}.

        Official method: https://docs.vapi.ai/calls/call-features
        There is no documented POST /call/{id}/end. The control URL must be
        read from the call record; its host can change between calls.
        """
        call = self.get_call(call_id)
        status = call.get("status") if isinstance(call, dict) else None
        if status in TERMINAL_STATUSES:
            raise VapiError(
                f"Call {call_id} is already {status}; live control only works while in progress."
            )
        control_url = None
        if isinstance(call, dict):
            monitor = call.get("monitor") or {}
            control_url = monitor.get("controlUrl")
        if not control_url:
            raise VapiError(
                f"Call {call_id} has no monitor.controlUrl (status={status!r}). "
                "Live control may be off (assistant.monitorPlan.controlEnabled=false) "
                "or the call is not active."
            )
        headers: dict[str, str] = {"Content-Type": "application/json"}
        # Control URLs are unauthenticated by default. If the assistant has
        # monitorPlan.controlAuthenticationEnabled, Vapi requires the *public*
        # API key (a private key is refused).
        if self.public_api_key:
            headers["Authorization"] = f"Bearer {self.public_api_key}"
        response = self.request(
            "POST",
            control_url,
            body={"type": "end-call"},
            headers=headers,
            authenticate=False,
            timeout=45.0,
        )
        return {
            "controlUrl": control_url,
            "command": {"type": "end-call"},
            "response": response,
            "call": call,
        }

    def poll_until_ended(
        self,
        call_id: str,
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
        log: Callable[[str], None] | None = None,
    ) -> Any:
        emit = log or (lambda _message: None)
        started = self.clock()
        last_status: str | None = None
        while True:
            call = self.get_call(call_id)
            status = call.get("status") if isinstance(call, dict) else None
            status_label = status if status is not None else "unknown"
            if status != last_status:
                emit(f"[{_now_iso()}] call {call_id} status: {status_label}")
                last_status = status
            if status in TERMINAL_STATUSES:
                return call
            if self.clock() - started >= timeout:
                raise VapiError(
                    f"Timed out after {timeout:.0f}s waiting for call {call_id} "
                    f"to end (last status: {status_label})."
                )
            self.sleeper(interval)
