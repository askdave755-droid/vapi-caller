#!/usr/bin/env python3
"""Tests for .env loading and CLI / env / config precedence."""

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

from vapi_caller.cli import main  # noqa: E402
from vapi_caller.config import (  # noqa: E402
    ENV_ASSISTANT_ID,
    ENV_PHONE_NUMBER_ID,
    load_dotenv,
    parse_assistant,
    resolve_phone_number_id,
)


class DotenvTests(unittest.TestCase):
    def test_loads_values_without_overwriting_existing_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "\n".join(
                    [
                        "# comment",
                        "export VAPI_PHONE_NUMBER_ID=pn-from-dotenv",
                        'VAPI_ASSISTANT_ID="asst-from-dotenv"',
                        "VAPI_API_KEY=dotenv-secret-should-not-be-printed",
                        "ALREADY_SET=from-file",
                    ]
                )
                + "\n"
            )
            with patch.dict(os.environ, {"ALREADY_SET": "from-env"}, clear=False):
                os.environ.pop("VAPI_PHONE_NUMBER_ID", None)
                os.environ.pop("VAPI_ASSISTANT_ID", None)
                os.environ.pop("VAPI_API_KEY", None)
                loaded = load_dotenv(path)
                self.assertEqual(loaded, path)
                self.assertEqual(os.environ["VAPI_PHONE_NUMBER_ID"], "pn-from-dotenv")
                self.assertEqual(os.environ["VAPI_ASSISTANT_ID"], "asst-from-dotenv")
                self.assertEqual(os.environ["VAPI_API_KEY"], "dotenv-secret-should-not-be-printed")
                self.assertEqual(os.environ["ALREADY_SET"], "from-env")
            # Cleanup keys we may have set, but restore is handled by leaving the test env
            os.environ.pop("VAPI_PHONE_NUMBER_ID", None)
            os.environ.pop("VAPI_ASSISTANT_ID", None)
            os.environ.pop("VAPI_API_KEY", None)

    def test_missing_file_returns_none(self):
        self.assertIsNone(load_dotenv(Path("/tmp/does-not-exist-vapi-caller.env")))


class PrecedenceTests(unittest.TestCase):
    def test_cli_overrides_env_and_env_overrides_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "phoneNumberId": "pn-from-config",
                        "assistantId": "asst-from-config",
                        "customer": "+15550000001",
                    }
                )
            )
            env_file = Path(tmp) / ".env"
            env_file.write_text(
                "VAPI_PHONE_NUMBER_ID=pn-from-env\nVAPI_ASSISTANT_ID=asst-from-env\n"
            )
            base_env = {
                ENV_PHONE_NUMBER_ID: "pn-from-env",
                ENV_ASSISTANT_ID: "asst-from-env",
            }
            # Env wins over config when no CLI ids are passed.
            stdout = io.StringIO()
            with patch.dict(os.environ, base_env, clear=False):
                with patch("sys.stdout", stdout):
                    code = main(
                        [
                            "--config",
                            str(config_path),
                            "--dry-run",
                            "call",
                            "--customer",
                            "+15550000001",
                        ]
                    )
            self.assertEqual(code, 0)
            out = stdout.getvalue()
            self.assertIn("pn-from-env", out)
            self.assertIn("asst-from-env", out)
            self.assertNotIn("pn-from-config", out)
            self.assertNotIn("asst-from-config", out)

            # CLI flags win over env.
            stdout = io.StringIO()
            with patch.dict(os.environ, base_env, clear=False):
                with patch("sys.stdout", stdout):
                    code = main(
                        [
                            "--config",
                            str(config_path),
                            "--dry-run",
                            "call",
                            "--phone-number-id",
                            "pn-from-cli",
                            "--assistant-id",
                            "asst-from-cli",
                            "--customer",
                            "+15550000001",
                        ]
                    )
            self.assertEqual(code, 0)
            out = stdout.getvalue()
            self.assertIn("pn-from-cli", out)
            self.assertIn("asst-from-cli", out)
            self.assertNotIn("pn-from-env", out)

    def test_env_file_flag_loads_ids_for_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "custom.env"
            env_file.write_text(
                "VAPI_PHONE_NUMBER_ID=pn-from-envfile\nVAPI_ASSISTANT_ID=asst-from-envfile\n"
            )
            # Ensure process env does not already supply these.
            cleared = {}
            for key in (ENV_PHONE_NUMBER_ID, ENV_ASSISTANT_ID, "VAPI_API_KEY"):
                if key in os.environ:
                    cleared[key] = os.environ[key]
                    del os.environ[key]
            try:
                stdout = io.StringIO()
                with patch("sys.stdout", stdout):
                    code = main(
                        [
                            "--env-file",
                            str(env_file),
                            "--dry-run",
                            "call",
                            "--customer",
                            "+15550000002",
                        ]
                    )
                self.assertEqual(code, 0)
                out = stdout.getvalue()
                self.assertIn("pn-from-envfile", out)
                self.assertIn("asst-from-envfile", out)
                self.assertNotIn("dotenv-secret", out)
            finally:
                os.environ.pop(ENV_PHONE_NUMBER_ID, None)
                os.environ.pop(ENV_ASSISTANT_ID, None)
                os.environ.update(cleared)

    def test_resolve_helpers(self):
        with patch.dict(os.environ, {ENV_PHONE_NUMBER_ID: "pn-env"}, clear=False):
            self.assertEqual(resolve_phone_number_id("pn-cli", {"phoneNumberId": "pn-cfg"}), "pn-cli")
            self.assertEqual(resolve_phone_number_id(None, {"phoneNumberId": "pn-cfg"}), "pn-env")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(ENV_ASSISTANT_ID, None)
            assistant_id, assistant = parse_assistant(None, None, {"assistantId": "asst-cfg"})
            self.assertEqual(assistant_id, "asst-cfg")
            self.assertIsNone(assistant)


if __name__ == "__main__":
    unittest.main()
