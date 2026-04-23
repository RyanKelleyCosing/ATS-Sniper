"""Unit tests for Azure Function trigger option parsing."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from azure_function_helpers import coerce_bool, normalize_trigger_options


def test_coerce_bool_handles_common_truthy_and_falsey_values() -> None:
    assert coerce_bool("true") is True
    assert coerce_bool("YES") is True
    assert coerce_bool("0", default=True) is False
    assert coerce_bool(None, default=True) is True


def test_normalize_trigger_options_prefers_body_payload() -> None:
    options = normalize_trigger_options(
        {"run_type": "morning", "dry_run": "false"},
        {"run_type": "afternoon", "dry_run": True, "skip_tailor": True, "v2": True},
    )

    assert options == {
        "dry_run": True,
        "skip_tailor": True,
        "v3_mode": False,
        "run_type": "afternoon",
    }


def test_normalize_trigger_options_falls_back_to_full_for_invalid_run_type() -> None:
    options = normalize_trigger_options({"run_type": "nightly"}, {})

    assert options["run_type"] == "full"