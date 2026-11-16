"""Extract, save, and print a short summary of a finished Vapi call."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_RESULTS_DIR = Path.cwd() / "results"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _truncate(text: str, limit: int = 500) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _parse_iso(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def duration_seconds(call: dict[str, Any]) -> float | None:
    """Duration is not a first-class field; docs say cost minutes should match endedAt - startedAt."""
    started = _parse_iso(call.get("startedAt"))
    ended = _parse_iso(call.get("endedAt"))
    if not started or not ended:
        return None
    return max(0.0, (ended - started).total_seconds())


def recording_url(call: dict[str, Any]) -> str | None:
    artifact = call.get("artifact") or {}
    if not isinstance(artifact, dict):
        return None
    for key in ("recordingUrl", "presignedMonoUrl", "stereoRecordingUrl", "presignedStereoUrl"):
        value = artifact.get(key)
        if isinstance(value, str) and value:
            return value
    recording = artifact.get("recording")
    if isinstance(recording, str) and recording:
        return recording
    if isinstance(recording, dict):
        for key in ("url", "monoUrl", "stereoUrl", "recordingUrl"):
            value = recording.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def extract_results(call: dict[str, Any]) -> dict[str, Any]:
    artifact = call.get("artifact") if isinstance(call.get("artifact"), dict) else {}
    analysis = call.get("analysis") if isinstance(call.get("analysis"), dict) else {}
    duration = duration_seconds(call)
    return {
        "id": call.get("id"),
        "status": call.get("status"),
        "endedReason": call.get("endedReason"),
        "startedAt": call.get("startedAt"),
        "endedAt": call.get("endedAt"),
        "durationSeconds": duration,
        "cost": call.get("cost"),
        "transcript": artifact.get("transcript"),
        "summary": analysis.get("summary"),
        "recordingUrl": recording_url(call),
        "assistantId": call.get("assistantId"),
        "phoneNumberId": call.get("phoneNumberId"),
        "customer": (call.get("customer") or {}).get("number")
        if isinstance(call.get("customer"), dict)
        else call.get("customer"),
    }


def save_results(call: dict[str, Any], results_dir: Path) -> tuple[Path, dict[str, Any]]:
    call_id = call.get("id") or "unknown-call"
    results_dir.mkdir(parents=True, exist_ok=True)
    summary = extract_results(call)
    payload = {"summary": summary, "call": call}
    path = results_dir / f"{call_id}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path, summary


def format_summary(summary: dict[str, Any]) -> str:
    duration = summary.get("durationSeconds")
    if isinstance(duration, (int, float)):
        duration_text = f"{duration:.1f}s"
    else:
        duration_text = "n/a"
    cost = summary.get("cost")
    cost_text = f"${cost:.4f}" if isinstance(cost, (int, float)) else "n/a"
    transcript = summary.get("transcript") or ""
    transcript_preview = _truncate(transcript.replace("\n", " "), 160) if transcript else "n/a"
    call_summary = summary.get("summary") or "n/a"
    recording = summary.get("recordingUrl") or "n/a"
    return (
        f"Call {summary.get('id')}\n"
        f"  status:        {summary.get('status')}\n"
        f"  endedReason:   {summary.get('endedReason')}\n"
        f"  duration:      {duration_text}\n"
        f"  cost:          {cost_text}\n"
        f"  summary:       {_truncate(str(call_summary), 240)}\n"
        f"  recordingUrl:  {recording}\n"
        f"  transcript:    {transcript_preview}"
    )
