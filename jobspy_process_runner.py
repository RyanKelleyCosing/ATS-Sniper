#!/usr/bin/env python3
"""Run JobSpy queries under a compatible interpreter and write JSON-safe results."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import date, datetime
import json
import math
from pathlib import Path
from typing import Any

from jobspy import scrape_jobs as scrape_jobspy_jobs


def _coerce_jobspy_records(jobspy_results: Any) -> list[dict[str, Any]]:
    """Normalize JobSpy results into plain record dictionaries."""
    if jobspy_results is None:
        return []

    if hasattr(jobspy_results, "to_dict"):
        try:
            records = jobspy_results.to_dict("records")
        except TypeError:
            records = jobspy_results.to_dict()
        if isinstance(records, list):
            return [dict(record) for record in records if isinstance(record, Mapping)]
        return []

    if isinstance(jobspy_results, list):
        return [dict(record) for record in jobspy_results if isinstance(record, Mapping)]

    return []


def _json_safe(value: Any) -> Any:
    """Convert JobSpy values into JSON-safe built-in types."""
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            return str(value)
    if isinstance(value, str | int | bool) or value is None:
        return value
    return str(value)


def run_queries(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Execute all JobSpy queries from the input payload."""
    results: list[dict[str, Any]] = []
    for query_request in payload.get("queries", []):
        if not isinstance(query_request, Mapping):
            results.append({"records": [], "error": "Invalid JobSpy query payload"})
            continue

        search_kwargs = dict(query_request.get("search_kwargs", {}))
        try:
            jobspy_results = scrape_jobspy_jobs(**search_kwargs)
            records = _coerce_jobspy_records(jobspy_results)
            results.append({"records": _json_safe(records), "error": ""})
        except Exception as exc:  # noqa: BLE001
            results.append({"records": [], "error": str(exc)})

    return {"results": results}


def main() -> int:
    """Load a query payload, execute JobSpy, and persist the results."""
    parser = argparse.ArgumentParser(description="Run JobSpy queries and persist JSON-safe results")
    parser.add_argument("--input", required=True, help="Path to the JSON input payload")
    parser.add_argument("--output", required=True, help="Path to the JSON output payload")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result_payload = run_queries(payload if isinstance(payload, Mapping) else {})
    output_path.write_text(json.dumps(result_payload), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())