"""Small CLI for placing, monitoring, and summarizing outbound Vapi phone calls."""

from __future__ import annotations

__version__ = "0.1.0"

from vapi_caller.client import VapiClient, VapiError
from vapi_caller.results import extract_results, format_summary, save_results

__all__ = [
    "VapiClient",
    "VapiError",
    "extract_results",
    "format_summary",
    "save_results",
    "__version__",
]
