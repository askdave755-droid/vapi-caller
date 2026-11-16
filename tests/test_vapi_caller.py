#!/usr/bin/env python3
"""Unit tests for polling, result handling, dry-run payloads, and hang-up.

These tests use a fake HTTP layer. They never call api.vapi.ai and never
place a phone call.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from vapi_caller.cli import build_create_payload, main  # noqa: E402
from vapi_caller.client import REDACTED, VapiClient, VapiError, _redact_headers, _safe_url  # noqa: E402
from vapi_caller.results import (  # noqa: E402
    duration_seconds,
    extract_results,
    format_summary,
    recording_url,
    save_results,
)


ENDED_CALL = {
    "id": "call-123",
    "status": "ended",
    "endedReason": "assistant-ended-call",
    "startedAt": "2026-11-16T14:00:00.000Z",
    "endedAt": "2026-11-16T14:02:15.000Z",
    "cost": 0.42,
    "assistantId": "asst-1",
    "phoneNumberId": "pn-1",
    "customer": {"number": "+14155550100"},
    "analysis": {"summary": "Customer asked about a heating tune-up and asked to be called back Tuesday."},
    "artifact": {
        "transcript": "AI: Hi, this is a sample assistant.\nUser: Can you come Tuesday?",
        "recordingUrl": "https://example.com/recordings/call-123.wav",
    },
}


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class FakeHttp:
    def __init__(self, responses: list[tuple[int, dict, bytes | dict | str]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=30.0):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers or {}),
                "body": body,
                "timeout": timeout,
            }
        )
        if not self.responses:
            raise AssertionError(f"Unexpected HTTP call: {method} {url}")
        status, response_headers, payload = self.responses.pop(0)
        if isinstance(payload, (dict, list)):
            raw = json.dumps(payload).encode("utf-8")
        elif isinstance(payload, str):
            raw = payload.encode("utf-8")
        else:
            raw = payload
        if status >= 400:
            raise VapiError(f"HTTP {status} {method} {_safe_url(url)}: {raw.decode('utf-8', 'replace')}")
        return status, response_headers, raw


def queued(status: str, **extra) -> dict:
    body = {
        "id": "call-123",
        "status": status,
        "assistantId": "asst-1",
        "phoneNumberId": "pn-1",
        "customer": {"number": "+14155550100"},
        "monitor": {
            "controlUrl": "https://phone-call-websocket.example.vapi.ai/call-123/control",
            "listenUrl": "wss://phone-call-websocket.example.vapi.ai/call-123/listen",
        },
    }
    body.update(extra)
    return body


class ResultTests(unittest.TestCase):
    def test_extracts_status_reason_duration_cost_transcript_summary_recording(self):
        summary = extract_results(ENDED_CALL)
        self.assertEqual(summary["id"], "call-123")
        self.assertEqual(summary["status"], "ended")
        self.assertEqual(summary["endedReason"], "assistant-ended-call")
        self.assertEqual(summary["durationSeconds"], 135.0)
        self.assertEqual(summary["cost"], 0.42)
        self.assertIn("heating tune-up", summary["summary"])
        self.assertIn("sample assistant", summary["transcript"])
        self.assertEqual(summary["recordingUrl"], "https://example.com/recordings/call-123.wav")
        self.assertEqual(summary["customer"], "+14155550100")

    def test_recording_falls_back_to_presigned_mono_url(self):
        call = {
            "id": "call-9",
            "artifact": {"presignedMonoUrl": "https://signed.example/mono.wav"},
        }
        self.assertEqual(recording_url(call), "https://signed.example/mono.wav")

    def test_recording_reads_nested_recording_object(self):
        call = {"artifact": {"recording": {"url": "https://example.com/from-object.wav"}}}
        self.assertEqual(recording_url(call), "https://example.com/from-object.wav")

    def test_duration_none_when_timestamps_missing(self):
        self.assertIsNone(duration_seconds({"status": "queued"}))

    def test_save_results_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, summary = save_results(ENDED_CALL, Path(tmp))
            self.assertEqual(path, Path(tmp) / "call-123.json")
            saved = json.loads(path.read_text())
            self.assertEqual(saved["summary"], summary)
            self.assertEqual(saved["call"]["id"], "call-123")
            printed = format_summary(summary)
            self.assertIn("endedReason", printed)
            self.assertIn("0.4200", printed)
            self.assertIn("135.0s", printed)


class PayloadTests(unittest.TestCase):
    def test_build_payload_with_assistant_id(self):
        payload = build_create_payload(
            phone_number_id="pn-1",
            customer="+14155550100",
            assistant_id="asst-1",
            assistant=None,
            name="Test call",
        )
        self.assertEqual(
            payload,
            {
                "phoneNumberId": "pn-1",
                "customer": {"number": "+14155550100"},
                "assistantId": "asst-1",
                "name": "Test call",
            },
        )

    def test_build_payload_with_inline_assistant(self):
        assistant = {"name": "Sample", "firstMessage": "Hi there."}
        payload = build_create_payload(
            phone_number_id="pn-1",
            customer="+14155550100",
            assistant_id=None,
            assistant=assistant,
            name=None,
        )
        self.assertEqual(payload["assistant"], assistant)
        self.assertNotIn("assistantId", payload)

    def test_rejects_missing_fields_and_both_assistant_forms(self):
        with self.assertRaises(VapiError):
            build_create_payload(
                phone_number_id=None, customer="+1", assistant_id="a", assistant=None, name=None
            )
        with self.assertRaises(VapiError):
            build_create_payload(
                phone_number_id="pn", customer=None, assistant_id="a", assistant=None, name=None
            )
        with self.assertRaises(VapiError):
            build_create_payload(
                phone_number_id="pn", customer="+1", assistant_id=None, assistant=None, name=None
            )
        with self.assertRaises(VapiError):
            build_create_payload(
                phone_number_id="pn",
                customer="+1",
                assistant_id="a",
                assistant={"name": "x"},
                name=None,
            )


class PollTests(unittest.TestCase):
    def test_poll_logs_status_changes_and_stops_when_ended(self):
        http = FakeHttp(
            [
                (200, {}, queued("queued")),
                (200, {}, queued("ringing")),
                (200, {}, queued("in-progress")),
                (200, {}, ENDED_CALL),
            ]
        )
        clock = FakeClock()
        logs: list[str] = []
        client = VapiClient("test-key-should-never-appear-in-logs", http=http, sleeper=clock.sleep, clock=clock)
        result = client.poll_until_ended("call-123", interval=0.5, timeout=30, log=logs.append)
        self.assertEqual(result["status"], "ended")
        statuses = [line.split("status: ", 1)[1] for line in logs]
        self.assertEqual(statuses, ["queued", "ringing", "in-progress", "ended"])
        self.assertEqual(clock.sleeps, [0.5, 0.5, 0.5])
        self.assertEqual(len(http.calls), 4)
        for call in http.calls:
            self.assertEqual(call["method"], "GET")
            self.assertTrue(call["url"].endswith("/call/call-123"))
            self.assertEqual(call["headers"]["Authorization"], "Bearer test-key-should-never-appear-in-logs")
        joined = "\n".join(logs)
        self.assertNotIn("test-key-should-never-appear-in-logs", joined)

    def test_poll_does_not_log_duplicate_status(self):
        http = FakeHttp(
            [
                (200, {}, queued("in-progress")),
                (200, {}, queued("in-progress")),
                (200, {}, ENDED_CALL),
            ]
        )
        clock = FakeClock()
        logs: list[str] = []
        client = VapiClient("k", http=http, sleeper=clock.sleep, clock=clock)
        client.poll_until_ended("call-123", interval=1, timeout=30, log=logs.append)
        statuses = [line.split("status: ", 1)[1] for line in logs]
        self.assertEqual(statuses, ["in-progress", "ended"])

    def test_poll_times_out(self):
        http = FakeHttp([(200, {}, queued("ringing"))] * 5)
        clock = FakeClock()
        client = VapiClient("k", http=http, sleeper=clock.sleep, clock=clock)
        with self.assertRaises(VapiError) as ctx:
            client.poll_until_ended("call-123", interval=2, timeout=4, log=lambda _: None)
        self.assertIn("Timed out", str(ctx.exception))


class EndCallTests(unittest.TestCase):
    def test_end_posts_end_call_to_control_url(self):
        http = FakeHttp(
            [
                (200, {}, queued("in-progress")),
                (200, {}, {"ok": True}),
            ]
        )
        client = VapiClient("private-secret-key", http=http)
        result = client.end_call("call-123")
        self.assertEqual(result["command"], {"type": "end-call"})
        self.assertEqual(http.calls[0]["method"], "GET")
        self.assertTrue(http.calls[0]["url"].endswith("/call/call-123"))
        self.assertEqual(http.calls[1]["method"], "POST")
        self.assertEqual(http.calls[1]["url"], "https://phone-call-websocket.example.vapi.ai/call-123/control")
        self.assertEqual(json.loads(http.calls[1]["body"]), {"type": "end-call"})
        self.assertNotIn("Authorization", http.calls[1]["headers"])

    def test_end_uses_public_key_when_set(self):
        http = FakeHttp([(200, {}, queued("in-progress")), (200, {}, {})])
        client = VapiClient("private-secret-key", http=http, public_api_key="public-key")
        client.end_call("call-123")
        self.assertEqual(http.calls[1]["headers"]["Authorization"], "Bearer public-key")
        self.assertNotIn("private-secret-key", http.calls[1]["headers"]["Authorization"])

    def test_end_refuses_already_ended_call(self):
        http = FakeHttp([(200, {}, ENDED_CALL)])
        client = VapiClient("k", http=http)
        with self.assertRaises(VapiError) as ctx:
            client.end_call("call-123")
        self.assertIn("already ended", str(ctx.exception))
        self.assertEqual(len(http.calls), 1)

    def test_end_requires_control_url(self):
        http = FakeHttp([(200, {}, {"id": "call-123", "status": "queued"})])
        client = VapiClient("k", http=http)
        with self.assertRaises(VapiError) as ctx:
            client.end_call("call-123")
        self.assertIn("controlUrl", str(ctx.exception))


class ListTests(unittest.TestCase):
    def test_list_calls_query(self):
        http = FakeHttp([(200, {}, [{"id": "call-1", "status": "ended"}])])
        client = VapiClient("k", http=http)
        items = client.list_calls(limit=5, assistant_id="asst-1")
        self.assertEqual(items[0]["id"], "call-1")
        self.assertEqual(http.calls[0]["method"], "GET")
        self.assertIn("limit=5", http.calls[0]["url"])
        self.assertIn("assistantId=asst-1", http.calls[0]["url"])


class RedactionTests(unittest.TestCase):
    def test_authorization_header_is_redacted(self):
        redacted = _redact_headers({"Authorization": "Bearer super-secret", "Content-Type": "application/json"})
        self.assertEqual(redacted["Authorization"], REDACTED)
        self.assertEqual(redacted["Content-Type"], "application/json")

    def test_http_error_does_not_include_key_in_message(self):
        def boom(method, url, *, headers=None, body=None, timeout=30.0):
            raise VapiError(f"HTTP 401 {method} {url}: Unauthorized")

        client = VapiClient("super-secret-api-key", http=boom)
        with self.assertRaises(VapiError) as ctx:
            client.get_call("call-123")
        self.assertNotIn("super-secret-api-key", str(ctx.exception))


class CliDryRunTests(unittest.TestCase):
    def test_call_dry_run_prints_payload_and_sends_nothing(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            code = main(
                [
                    "--dry-run",
                    "call",
                    "--phone-number-id",
                    "pn-1",
                    "--assistant-id",
                    "asst-1",
                    "--customer",
                    "+14155550100",
                    "--name",
                    "Sample",
                ]
            )
        self.assertEqual(code, 0)
        out = stdout.getvalue()
        self.assertIn("DRY RUN", out)
        self.assertIn("POST https://api.vapi.ai/call", out)
        self.assertIn('"assistantId": "asst-1"', out)
        self.assertIn('"phoneNumberId": "pn-1"', out)
        self.assertIn('"+14155550100"', out)
        self.assertIn("***REDACTED***", out)
        self.assertNotIn("Bearer None", out)

    def test_call_without_confirm_or_dry_run_is_refused(self):
        stderr = io.StringIO()
        with patch.dict(os.environ, {"VAPI_API_KEY": "should-not-be-used"}, clear=False):
            with patch("sys.stderr", stderr):
                code = main(
                    [
                        "call",
                        "--phone-number-id",
                        "pn-1",
                        "--assistant-id",
                        "asst-1",
                        "--customer",
                        "+14155550100",
                    ]
                )
        self.assertEqual(code, 1)
        err = stderr.getvalue()
        self.assertIn("Refusing to place a live call", err)
        self.assertNotIn("should-not-be-used", err)

    def test_list_and_end_dry_run(self):
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            self.assertEqual(main(["--dry-run", "list", "--limit", "10"]), 0)
            self.assertEqual(main(["--dry-run", "end", "--call-id", "call-123"]), 0)
            self.assertEqual(main(["--dry-run", "results", "--call-id", "call-123"]), 0)
            self.assertEqual(main(["--dry-run", "list-assistants"]), 0)
        out = stdout.getvalue()
        self.assertIn("GET https://api.vapi.ai/call?limit=10", out)
        self.assertIn('"type": "end-call"', out)
        self.assertIn("GET https://api.vapi.ai/assistant", out)

    def test_config_file_and_inline_assistant(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "phoneNumberId": "pn-from-file",
                        "customer": "+15551234567",
                        "assistant": {"name": "Inline", "firstMessage": "Hello from config."},
                    }
                )
            )
            stdout = io.StringIO()
            with patch("sys.stdout", stdout):
                code = main(["--config", str(config_path), "--dry-run", "call"])
            self.assertEqual(code, 0)
            out = stdout.getvalue()
            self.assertIn("pn-from-file", out)
            self.assertIn("+15551234567", out)
            self.assertIn("Hello from config.", out)
            self.assertNotIn("assistantId", out)


if __name__ == "__main__":
    unittest.main()
