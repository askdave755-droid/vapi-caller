# vapi-caller

A small command-line tool for placing, watching, hanging up, and summarizing outbound phone calls through [Vapi](https://docs.vapi.ai).

It talks only to the documented Vapi REST API:

| Action | Official endpoint |
| --- | --- |
| Place an outbound call | `POST https://api.vapi.ai/call` ([Create Call](https://docs.vapi.ai/api-reference/calls/create), [Outbound Calling](https://docs.vapi.ai/calls/outbound-calling)) |
| Monitor a call | `GET https://api.vapi.ai/call/{id}` until `status` is `ended` ([Get Call](https://docs.vapi.ai/api-reference/calls/get)) |
| End a live call | `POST {call.monitor.controlUrl}` with `{"type": "end-call"}` ([Live call control](https://docs.vapi.ai/calls/call-features)) |
| List recent calls | `GET https://api.vapi.ai/call` ([List Calls](https://docs.vapi.ai/api-reference/calls/list)) |
| List assistants / numbers | `GET https://api.vapi.ai/assistant`, `GET https://api.vapi.ai/phone-number` (read-only) |

There is no documented `POST /call/{id}/end`. Hang-up uses the call's `monitor.controlUrl` from the create or get-call response. Do not invent that URL; read it from the call record because the host can change between calls.

Python 3.10+ is enough. There are no third-party dependencies.

## Prerequisites

- A [Vapi](https://dashboard.vapi.ai) account and a **private API key**
- An **imported, outbound-capable phone number** in Vapi, and its `phoneNumberId`. Free Vapi numbers cannot place outbound calls; import one from Twilio, Telnyx, or another supported provider.
- A saved **assistant** (and its `assistantId`), or a transient assistant JSON file (see `examples/assistant.example.json`)
- The **customer number** you want to dial, in E.164 form (for example `+14155550100`)
- Explicit approval before any live `--confirm-live-call` run

If an assistant has `monitorPlan.controlAuthenticationEnabled` set, you will also need your **public** API key as `VAPI_PUBLIC_API_KEY` (not the private key) so hang-up commands are accepted.

## Clone and install

```bash
git clone https://github.com/askdave755-droid/vapi-caller.git
cd vapi-caller
python3 -m pip install -e .
```

The project has no runtime dependencies. `pip install -e .` is optional; it registers the `vapi-caller` command. Without installing, you can run:

```bash
PYTHONPATH=src python3 -m vapi_caller --help
```

## Configuration

Copy the example env file and fill in your values. **Never commit `.env`.**

```bash
cp .env.example .env
```

Supported environment variables (no values are hardcoded, and secrets are never printed):

| Variable | Required | Purpose |
| --- | --- | --- |
| `VAPI_API_KEY` | Yes, for any live API request | Private Vapi API key |
| `VAPI_PHONE_NUMBER_ID` | For placing a call | ID of the number to call from |
| `VAPI_ASSISTANT_ID` | For placing a call, unless you pass an inline assistant | Saved assistant ID |
| `VAPI_PUBLIC_API_KEY` | Only if live-call control requires auth | Public Vapi API key |

You can also pass a JSON config file (`--config examples/config.example.json`) and/or CLI flags. Precedence is:

1. CLI flags (highest)
2. Environment variables, including values loaded from `.env`
3. The optional JSON config file

`.env` is loaded automatically from the current directory, or from `--env-file path`. Loading uses the standard library only; existing environment variables are not overwritten.

```bash
# flags override env
vapi-caller --dry-run call --phone-number-id pn_xxx --assistant-id asst_xxx --customer +14155550100

# env / .env supply the IDs
vapi-caller --dry-run call --customer +14155550100

# optional JSON config (still overridden by flags and env)
vapi-caller --config examples/config.example.json --dry-run call
```

## Live-call safety gate

The private API key is never printed. A real outbound call is **not** placed unless you pass `--confirm-live-call`. Use `--dry-run` first to inspect the request. Do not place a live call without explicit approval for that number.

```bash
# preview only — no network, no call
vapi-caller --dry-run call --customer +14155550100

# live call — only after approval for that specific number
vapi-caller call --customer +14155550100 --confirm-live-call
```

## Usage

All commands accept `--dry-run` (prints the request, sends nothing), `--config`, and `--env-file`.

### Preview an outbound call (no network)

```bash
vapi-caller --dry-run call \
  --phone-number-id YOUR_PHONE_NUMBER_ID \
  --assistant-id YOUR_ASSISTANT_ID \
  --customer +14155550100
```

Use a transient assistant from a file instead of a saved ID:

```bash
vapi-caller --dry-run call \
  --phone-number-id YOUR_PHONE_NUMBER_ID \
  --assistant-file examples/assistant.example.json \
  --customer +14155550100
```

### Place a live call, poll until it ends, and save results

```bash
vapi-caller call --customer +14155550100 --confirm-live-call
```

This `POST`s `/call`, then polls `GET /call/{id}` (default every 2 seconds, 15 minute timeout), logs each status change (`queued` → `ringing` → `in-progress` → `ended`, and so on), writes `results/<callId>.json`, and prints a short summary.

To create the call and exit without waiting:

```bash
vapi-caller call --customer +14155550100 --confirm-live-call --no-watch
```

### Monitor an existing call

```bash
vapi-caller status --call-id CALL_ID
vapi-caller status --call-id CALL_ID --watch --save
```

### Hang up a live call

```bash
vapi-caller end --call-id CALL_ID
vapi-caller --dry-run end --call-id CALL_ID
```

The tool first `GET`s the call, then `POST`s `{"type": "end-call"}` to `monitor.controlUrl`. It will refuse if the call has already ended or has no control URL.

### Fetch and save results

```bash
vapi-caller results --call-id CALL_ID
```

The saved file is `results/<callId>.json` (created in the current working directory unless you pass `--results-dir`). It includes both a compact summary and the raw call object. The summary pulls:

- `status`, `endedReason`
- `durationSeconds` (from `endedAt - startedAt`; duration is not a first-class field)
- `cost` (USD)
- `artifact.transcript`
- `analysis.summary`
- recording URL (`artifact.recordingUrl`, falling back to `presignedMonoUrl` or `artifact.recording`)

### List recent calls, assistants, or phone numbers (read-only)

```bash
vapi-caller list --limit 20
vapi-caller list-assistants
vapi-caller list-phone-numbers
```

These are the only live API calls that are reasonable to run just to check a key. They do not place or end calls.

## Testing

```bash
cd vapi-caller
python3 -m unittest discover -s tests -v
```

If you have not installed the package, set `PYTHONPATH=src` first:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The tests use a fake HTTP layer. They cover payload construction, `--dry-run`, the live-call confirmation gate, status-change polling, hang-up via `controlUrl`, result extraction, environment/config precedence, `.env` loading, and API-key redaction. They never contact Vapi and never place a call.

## Troubleshooting

- **`VAPI_API_KEY is not set`** — export the private key or add it to `.env`. The tool will not print the value.
- **`HTTP 401`** — the private key is missing, revoked, or typo'd. Create or copy a key in the Vapi Dashboard.
- **`Refusing to place a live call`** — add `--dry-run`, or `--confirm-live-call` only after you have approval to dial that number.
- **Call is created but never connects** — free Vapi numbers cannot place outbound or international calls. Use an imported number and confirm its `phoneNumberId`.
- **`has no monitor.controlUrl`** — the call may already have ended, or `assistant.monitorPlan.controlEnabled` is `false`.
- **Hang-up returns Unauthorized** — if the assistant has `monitorPlan.controlAuthenticationEnabled`, set `VAPI_PUBLIC_API_KEY` to the *public* key. A private key is refused on the control URL.
- **`Timed out waiting for call ... to end`** — increase `--timeout`, or use `vapi-caller status --call-id ID --watch`.
- **Missing phone number or assistant** — pass `--phone-number-id` / `--assistant-id`, set `VAPI_PHONE_NUMBER_ID` / `VAPI_ASSISTANT_ID`, or use `--config` / `--assistant-file`.

## API notes

Call statuses from Get Call: `scheduled`, `queued`, `ringing`, `in-progress`, `forwarding`, `ended`, `not-found`, `deletion-failed`. Polling stops on `ended`, `not-found`, and `deletion-failed`.

Recording files eventually move behind authenticated artifact endpoints such as `GET /call/{id}/mono-recording`. The tool stores whatever URL the call record currently exposes (`recordingUrl` or a presigned URL).

## License

MIT. See [LICENSE](LICENSE). Replace `[YOUR NAME]` in `LICENSE` and `pyproject.toml` with the copyright holder.
