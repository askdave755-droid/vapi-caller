"""Command-line interface for vapi-caller."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

from vapi_caller.client import (
    API_BASE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT,
    TERMINAL_STATUSES,
    VapiClient,
    VapiError,
)
from vapi_caller.config import (
    ENV_API_KEY,
    load_config,
    load_dotenv,
    parse_assistant,
    resolve_api_key,
    resolve_customer,
    resolve_phone_number_id,
    resolve_public_api_key,
)
from vapi_caller.results import DEFAULT_RESULTS_DIR, extract_results, format_summary, save_results


def build_create_payload(
    *,
    phone_number_id: str | None,
    customer: str | None,
    assistant_id: str | None,
    assistant: dict[str, Any] | None,
    name: str | None,
) -> dict[str, Any]:
    if not phone_number_id:
        raise VapiError(
            "Missing phoneNumberId. Pass --phone-number-id, set VAPI_PHONE_NUMBER_ID, or put it in the config file."
        )
    if not customer:
        raise VapiError("Missing customer number. Pass --customer or set it in the config file.")
    if not assistant_id and not assistant:
        raise VapiError(
            "Missing assistant. Pass --assistant-id, --assistant, --assistant-file, "
            "set VAPI_ASSISTANT_ID, or put one in the config file."
        )
    if assistant_id and assistant:
        raise VapiError("Use either a saved assistantId or an inline assistant, not both.")
    payload: dict[str, Any] = {
        "phoneNumberId": phone_number_id,
        "customer": {"number": customer},
    }
    if assistant_id:
        payload["assistantId"] = assistant_id
    if assistant:
        payload["assistant"] = assistant
    if name:
        payload["name"] = name
    return payload


def print_dry_run(method: str, url: str, body: Any | None = None, query: dict[str, Any] | None = None) -> None:
    if query:
        from urllib.parse import urlencode

        url = f"{url}?{urlencode({k: v for k, v in query.items() if v is not None})}"
    print("DRY RUN — no request will be sent.")
    print(f"{method} {url}")
    print("Authorization: Bearer ***REDACTED***")
    if body is not None:
        print("Content-Type: application/json")
        print()
        print(json.dumps(body, indent=2))


def describe_list(items: Any, *, kind: str, fields: Iterable[str]) -> str:
    if items is None:
        return f"No {kind} returned."
    if not isinstance(items, list):
        return json.dumps(items, indent=2)
    if not items:
        return f"No {kind} found."
    lines = [f"{len(items)} {kind}:"]
    for item in items:
        if not isinstance(item, dict):
            lines.append(f"  {item}")
            continue
        parts = []
        for field in fields:
            value = item.get(field)
            if value is not None:
                parts.append(f"{field}={value}")
        lines.append("  " + ", ".join(parts) if parts else f"  {item.get('id', item)}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vapi-caller",
        description="Place, monitor, end, and summarize outbound Vapi phone calls.",
    )
    parser.add_argument("--config", type=Path, help="JSON config with phoneNumberId, assistantId or assistant, and customer.")
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Load environment variables from this file (stdlib). Defaults to ./.env if present. Existing env vars win.",
    )
    parser.add_argument(
        "--api-key-env",
        default=ENV_API_KEY,
        help=f"Environment variable holding the private Vapi API key (default: {ENV_API_KEY}).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the request and exit without calling the API.")
    parser.add_argument(
        "--results-dir",
        type=Path,
        default=None,
        help="Where to write results/<callId>.json (default: ./results).",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    call = sub.add_parser(
        "call",
        help="Place an outbound call (POST /call). Requires --confirm-live-call unless --dry-run.",
    )
    call.add_argument("--phone-number-id", help="Vapi phoneNumberId to call from (or VAPI_PHONE_NUMBER_ID).")
    call.add_argument("--assistant-id", help="Saved assistant ID (or VAPI_ASSISTANT_ID).")
    call.add_argument("--assistant", help="Saved assistant ID, path to a transient assistant JSON file, or inline JSON.")
    call.add_argument("--assistant-file", help="Path to a transient assistant JSON object.")
    call.add_argument("--customer", help="Customer phone number in E.164, e.g. +14155550100.")
    call.add_argument("--name", help="Optional label for your own reference.")
    call.add_argument(
        "--watch",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Poll until the call ends (default: true).",
    )
    call.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="Seconds between GET /call/{id} polls.")
    call.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Give up waiting for the call to end after this many seconds.")
    call.add_argument(
        "--confirm-live-call",
        action="store_true",
        help="Required to actually place a live call. Without this flag, only --dry-run is allowed.",
    )

    status = sub.add_parser("status", help="GET /call/{id}, optionally polling until it ends.")
    status.add_argument("--call-id", required=True)
    status.add_argument("--watch", action="store_true", help="Poll until the call reaches a terminal status.")
    status.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    status.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    status.add_argument("--save", action="store_true", help="If the call has ended, write results/<callId>.json.")

    end = sub.add_parser("end", help="Hang up a live call via POST {monitor.controlUrl} {type: end-call}.")
    end.add_argument("--call-id", required=True)

    results = sub.add_parser("results", help="Fetch a finished call, save results/<callId>.json, and print a short summary.")
    results.add_argument("--call-id", required=True)

    listing = sub.add_parser("list", help="List recent calls (GET /call).")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--assistant-id")
    listing.add_argument("--phone-number-id")

    assistants = sub.add_parser("list-assistants", help="List saved assistants (GET /assistant). Read-only.")
    assistants.add_argument("--limit", type=int, default=20)

    numbers = sub.add_parser("list-phone-numbers", help="List phone numbers (GET /phone-number). Read-only.")
    numbers.add_argument("--limit", type=int, default=20)

    return parser


def results_dir_from_args(args: argparse.Namespace) -> Path:
    return args.results_dir if args.results_dir is not None else DEFAULT_RESULTS_DIR


def client_from_args(args: argparse.Namespace, *, require_key: bool) -> VapiClient:
    api_key = resolve_api_key(args.api_key_env)
    if require_key and not api_key:
        raise VapiError(
            f"{args.api_key_env} is not set. Export your private Vapi API key "
            f"(Dashboard → API Keys) or add it to a .env file. The key is never printed."
        )
    return VapiClient(api_key, public_api_key=resolve_public_api_key())


def run_call_command(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    assistant_cli = args.assistant or args.assistant_id
    assistant_id, assistant = parse_assistant(assistant_cli, args.assistant_file, config)
    # An explicit --assistant-id wins over a bare --assistant ID if both are set.
    if args.assistant_id:
        assistant_id = args.assistant_id
        if args.assistant and str(args.assistant).strip().startswith("{"):
            raise VapiError("Use either --assistant-id or an inline --assistant, not both.")
        if args.assistant_file:
            raise VapiError("Use either --assistant-id or --assistant-file, not both.")
        assistant = None if assistant_id else assistant
    payload = build_create_payload(
        phone_number_id=resolve_phone_number_id(args.phone_number_id, config),
        customer=resolve_customer(args.customer, config),
        assistant_id=assistant_id,
        assistant=assistant,
        name=args.name or config.get("name"),
    )
    if args.dry_run:
        print_dry_run("POST", f"{API_BASE}/call", payload)
        return 0
    if not args.confirm_live_call:
        raise VapiError(
            "Refusing to place a live call. Re-run with --dry-run to inspect the request, "
            "or add --confirm-live-call after you have explicit approval to dial."
        )
    client = client_from_args(args, require_key=True)
    created = client.create_call(payload)
    call_id = created.get("id") if isinstance(created, dict) else None
    print(json.dumps(created if isinstance(created, dict) else {"response": created}, indent=2))
    if not call_id:
        raise VapiError("Create call succeeded but the response had no id.")
    print(f"Created call {call_id} (status={created.get('status')}).")
    if not args.watch:
        return 0
    ended = client.poll_until_ended(
        call_id,
        interval=args.poll_interval,
        timeout=args.timeout,
        log=print,
    )
    path, summary = save_results(ended, results_dir_from_args(args))
    print(format_summary(summary))
    print(f"Saved {path}")
    return 0


def run_status_command(args: argparse.Namespace) -> int:
    if args.dry_run:
        print_dry_run("GET", f"{API_BASE}/call/{args.call_id}")
        return 0
    client = client_from_args(args, require_key=True)
    if args.watch:
        call = client.poll_until_ended(
            args.call_id,
            interval=args.poll_interval,
            timeout=args.timeout,
            log=print,
        )
    else:
        call = client.get_call(args.call_id)
        from vapi_caller.results import _now_iso

        print(f"[{_now_iso()}] call {args.call_id} status: {call.get('status')}")
    if args.save or (isinstance(call, dict) and call.get("status") in TERMINAL_STATUSES):
        if args.save or args.watch:
            path, summary = save_results(call, results_dir_from_args(args))
            print(format_summary(summary))
            print(f"Saved {path}")
        else:
            print(json.dumps(extract_results(call), indent=2))
    else:
        print(json.dumps(call, indent=2))
    return 0


def run_end_command(args: argparse.Namespace) -> int:
    if args.dry_run:
        print_dry_run("GET", f"{API_BASE}/call/{args.call_id}")
        print()
        print("Then, if the call is in progress and has monitor.controlUrl:")
        print_dry_run("POST", "{controlUrl from GET /call/{id}}", {"type": "end-call"})
        print("Authorization on the control URL is omitted unless VAPI_PUBLIC_API_KEY is set.")
        return 0
    client = client_from_args(args, require_key=True)
    result = client.end_call(args.call_id)
    print(f"Sent end-call to {result['controlUrl']}")
    if result.get("response") is not None:
        print(json.dumps(result["response"], indent=2) if not isinstance(result["response"], str) else result["response"])
    return 0


def run_results_command(args: argparse.Namespace) -> int:
    if args.dry_run:
        print_dry_run("GET", f"{API_BASE}/call/{args.call_id}")
        print(f"Would write {results_dir_from_args(args) / (args.call_id + '.json')}")
        return 0
    client = client_from_args(args, require_key=True)
    call = client.get_call(args.call_id)
    path, summary = save_results(call, results_dir_from_args(args))
    print(format_summary(summary))
    print(f"Saved {path}")
    return 0


def run_list_command(args: argparse.Namespace) -> int:
    query = {"limit": args.limit, "assistantId": args.assistant_id, "phoneNumberId": args.phone_number_id}
    if args.dry_run:
        print_dry_run("GET", f"{API_BASE}/call", query=query)
        return 0
    client = client_from_args(args, require_key=True)
    items = client.list_calls(limit=args.limit, assistant_id=args.assistant_id, phone_number_id=args.phone_number_id)
    print(describe_list(items, kind="calls", fields=("id", "status", "endedReason", "createdAt", "customer")))
    return 0


def run_list_assistants_command(args: argparse.Namespace) -> int:
    if args.dry_run:
        print_dry_run("GET", f"{API_BASE}/assistant", query={"limit": args.limit})
        return 0
    client = client_from_args(args, require_key=True)
    items = client.list_assistants(limit=args.limit)
    print(describe_list(items, kind="assistants", fields=("id", "name", "updatedAt")))
    return 0


def run_list_phone_numbers_command(args: argparse.Namespace) -> int:
    if args.dry_run:
        print_dry_run("GET", f"{API_BASE}/phone-number", query={"limit": args.limit})
        return 0
    client = client_from_args(args, require_key=True)
    items = client.list_phone_numbers(limit=args.limit)
    print(describe_list(items, kind="phone numbers", fields=("id", "number", "name", "provider")))
    return 0


COMMANDS = {
    "call": run_call_command,
    "status": run_status_command,
    "end": run_end_command,
    "results": run_results_command,
    "list": run_list_command,
    "list-assistants": run_list_assistants_command,
    "list-phone-numbers": run_list_phone_numbers_command,
}


def _peek_env_file(argv: list[str] | None) -> Path | None:
    if not argv:
        return None
    for i, arg in enumerate(argv):
        if arg == "--env-file" and i + 1 < len(argv):
            return Path(argv[i + 1])
        if arg.startswith("--env-file="):
            return Path(arg.split("=", 1)[1])
    return None


def main(argv: list[str] | None = None) -> int:
    argv_list = list(argv) if argv is not None else None
    load_dotenv(_peek_env_file(argv_list))
    parser = build_parser()
    args = parser.parse_args(argv_list)
    try:
        return COMMANDS[args.command](args)
    except VapiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
