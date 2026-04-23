"""Discovery benchmark loading and overlap validation helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from utils.job_identity import build_job_identity_aliases
from utils.runtime_paths import project_root

DISCOVERY_BENCHMARK_ENV_VAR = "ATS_SNIPER_DISCOVERY_BENCHMARK_PATH"
DEFAULT_DISCOVERY_BENCHMARK_PATH = project_root() / "benchmarks" / "discovery_benchmark_targets.json"
MAX_BENCHMARK_DETAIL_ITEMS = 10


@dataclass(frozen=True, slots=True)
class DiscoveryBenchmarkTarget:
    """One expected discovery target used for overlap validation."""

    company: str
    title: str
    url: str
    source_family: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DiscoveryBenchmarkSet:
    """A named collection of discovery targets for validation."""

    name: str
    source_report: str
    targets: tuple[DiscoveryBenchmarkTarget, ...]
    match_strategy: str = "normalized_url_only"


def discovery_benchmark_path() -> Path:
    """Return the configured path for discovery benchmark targets."""
    override = os.getenv(DISCOVERY_BENCHMARK_ENV_VAR, "").strip()
    return Path(override) if override else DEFAULT_DISCOVERY_BENCHMARK_PATH


def normalize_benchmark_url(url: str) -> str:
    """Normalize job URLs for benchmark overlap comparisons."""
    raw_url = str(url).strip()
    if not raw_url:
        return ""

    split_url = urlsplit(raw_url)
    path = re.sub(r"/apply(?:/.*)?$", "", split_url.path)
    normalized = urlunsplit(
        (
            split_url.scheme.casefold(),
            split_url.netloc.casefold(),
            path.rstrip("/"),
            "",
            "",
        )
    ).rstrip("/")
    return normalized


def _require_text_field(raw_item: Mapping[str, Any], field_name: str) -> str:
    """Return a required string field from a benchmark payload item."""
    value = str(raw_item.get(field_name, "")).strip()
    if not value:
        raise ValueError(f"Discovery benchmark target is missing '{field_name}'.")
    return value


def _parse_target(raw_item: Mapping[str, Any]) -> DiscoveryBenchmarkTarget:
    """Convert one JSON payload item into a typed benchmark target."""
    return DiscoveryBenchmarkTarget(
        company=_require_text_field(raw_item, "company"),
        title=_require_text_field(raw_item, "title"),
        url=_require_text_field(raw_item, "url"),
        source_family=_require_text_field(raw_item, "source_family"),
        notes=str(raw_item.get("notes", "")).strip(),
    )


def load_discovery_benchmark_set(path: Path | None = None) -> DiscoveryBenchmarkSet:
    """Load the configured discovery benchmark set from disk."""
    benchmark_path = path or discovery_benchmark_path()
    payload = json.loads(benchmark_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Discovery benchmark payload must be a JSON object.")

    raw_targets = payload.get("targets", [])
    if not isinstance(raw_targets, list):
        raise ValueError("Discovery benchmark payload must include a 'targets' list.")

    targets = tuple(_parse_target(raw_target) for raw_target in raw_targets)
    if not targets:
        raise ValueError("Discovery benchmark payload must define at least one target.")

    return DiscoveryBenchmarkSet(
        name=str(payload.get("name", "Discovery benchmark")).strip() or "Discovery benchmark",
        source_report=str(payload.get("source_report", "")).strip(),
        targets=targets,
        match_strategy=str(payload.get("match_strategy", "normalized_url_only")).strip()
        or "normalized_url_only",
    )


def _index_discovered_jobs(discovered_jobs: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """Index discovered jobs by normalized URL for overlap checks."""
    indexed_jobs: dict[str, dict[str, str]] = {}
    for job in discovered_jobs:
        indexed_job = {
            "company": str(job.get("company", "Unknown")).strip(),
            "title": str(job.get("title", "Unknown")).strip(),
            "url": str(job.get("url", "")).strip(),
            "source_family": str(job.get("source_family", "unknown")).strip() or "unknown",
        }
        for alias in build_job_identity_aliases(job):
            indexed_jobs.setdefault(alias, indexed_job)
    return indexed_jobs


def _unique_indexed_jobs(indexed_jobs: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    """Collapse alias-indexed jobs into unique records for counting and extras."""
    unique_jobs: list[dict[str, str]] = []
    seen_job_keys: set[str] = set()

    for indexed_job in indexed_jobs.values():
        job_url = str(indexed_job.get("url", "")).strip()
        if job_url:
            dedupe_key = f"url::{job_url}"
        else:
            dedupe_key = "::".join(
                (
                    str(indexed_job.get("company", "Unknown")).strip().casefold(),
                    str(indexed_job.get("title", "Unknown")).strip().casefold(),
                    str(indexed_job.get("source_family", "unknown")).strip().casefold(),
                )
            )
        if dedupe_key in seen_job_keys:
            continue
        seen_job_keys.add(dedupe_key)
        unique_jobs.append(dict(indexed_job))

    return unique_jobs


def _build_benchmark_scope_summary(
    discovered_jobs: Sequence[Mapping[str, Any]],
    benchmark_set: DiscoveryBenchmarkSet,
    *,
    include_details: bool,
) -> dict[str, Any]:
    """Build counts and optional detail rows for one benchmark comparison scope."""
    indexed_jobs = _index_discovered_jobs(discovered_jobs)
    unique_indexed_jobs = _unique_indexed_jobs(indexed_jobs)
    hits: list[dict[str, str]] = []
    misses: list[dict[str, str]] = []
    source_family_summary: dict[str, dict[str, float | int]] = {}

    for target in benchmark_set.targets:
        family_summary = source_family_summary.setdefault(
            target.source_family,
            {"targets": 0, "hits": 0, "misses": 0, "overlap_rate": 0.0},
        )
        family_summary["targets"] = int(family_summary["targets"]) + 1
        target_aliases = build_job_identity_aliases(
            {
                "company": target.company,
                "title": target.title,
                "url": target.url,
                "source_family": target.source_family,
            }
        )
        matched_job = next((indexed_jobs[alias] for alias in target_aliases if alias in indexed_jobs), None)
        if matched_job:
            family_summary["hits"] = int(family_summary["hits"]) + 1
            if include_details:
                hits.append(
                    {
                        "company": target.company,
                        "title": target.title,
                        "url": target.url,
                        "source_family": target.source_family,
                        "matched_url": matched_job["url"],
                        "match_type": benchmark_set.match_strategy,
                    }
                )
            continue

        family_summary["misses"] = int(family_summary["misses"]) + 1
        if include_details:
            misses.append(
                {
                    "company": target.company,
                    "title": target.title,
                    "url": target.url,
                    "source_family": target.source_family,
                    "notes": target.notes,
                }
            )

    for family, summary in source_family_summary.items():
        targets = int(summary.get("targets", 0) or 0)
        hits_for_family = int(summary.get("hits", 0) or 0)
        summary["overlap_rate"] = round(hits_for_family / targets, 2) if targets else 0.0
        source_family_summary[family] = summary

    matched_urls = {hit["matched_url"] for hit in hits if hit.get("matched_url")}
    extra_count = 0
    extras: list[dict[str, str]] = []
    for indexed_job in unique_indexed_jobs:
        if indexed_job["url"] in matched_urls:
            continue
        extra_count += 1
        if include_details and len(extras) < MAX_BENCHMARK_DETAIL_ITEMS:
            extras.append(
                {
                    "company": indexed_job["company"],
                    "title": indexed_job["title"],
                    "url": indexed_job["url"],
                    "source_family": indexed_job["source_family"],
                }
            )

    scope_summary: dict[str, Any] = {
        "candidate_job_count": len(unique_indexed_jobs),
        "hit_count": len(hits) if include_details else sum(
            int(summary.get("hits", 0) or 0) for summary in source_family_summary.values()
        ),
        "miss_count": len(benchmark_set.targets) - sum(
            int(summary.get("hits", 0) or 0) for summary in source_family_summary.values()
        ),
        "extra_count": extra_count,
        "overlap_rate": round(
            sum(int(summary.get("hits", 0) or 0) for summary in source_family_summary.values())
            / len(benchmark_set.targets),
            2,
        ),
        "source_family_summary": dict(sorted(source_family_summary.items())),
    }
    if include_details:
        scope_summary["hits"] = hits[:MAX_BENCHMARK_DETAIL_ITEMS]
        scope_summary["misses"] = misses[:MAX_BENCHMARK_DETAIL_ITEMS]
        scope_summary["extras"] = extras
    return scope_summary


def _build_benchmark_drift(
    current_summary: Mapping[str, Any],
    previous_summary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compare the current benchmark summary against the previous stored run."""
    if not isinstance(previous_summary, Mapping) or not previous_summary:
        return {
            "available": False,
            "message": "No previous benchmark summary is available for drift comparison.",
        }

    current_families = current_summary.get("source_family_summary", {})
    previous_families = previous_summary.get("source_family_summary", {})
    family_deltas: dict[str, dict[str, float | int]] = {}
    for family in sorted(set(current_families) | set(previous_families)):
        current_family = current_families.get(family, {}) if isinstance(current_families, dict) else {}
        previous_family = previous_families.get(family, {}) if isinstance(previous_families, dict) else {}
        family_deltas[family] = {
            "delta_hits": int(current_family.get("hits", 0) or 0) - int(previous_family.get("hits", 0) or 0),
            "delta_misses": int(current_family.get("misses", 0) or 0)
            - int(previous_family.get("misses", 0) or 0),
            "delta_overlap_rate": round(
                float(current_family.get("overlap_rate", 0.0) or 0.0)
                - float(previous_family.get("overlap_rate", 0.0) or 0.0),
                2,
            ),
        }

    return {
        "available": True,
        "previous_completed_at": str(
            previous_summary.get("completed_at") or previous_summary.get("generated_at") or ""
        ).strip(),
        "delta_hit_count": int(current_summary.get("hit_count", 0) or 0)
        - int(previous_summary.get("hit_count", 0) or 0),
        "delta_miss_count": int(current_summary.get("miss_count", 0) or 0)
        - int(previous_summary.get("miss_count", 0) or 0),
        "delta_extra_count": int(current_summary.get("extra_count", 0) or 0)
        - int(previous_summary.get("extra_count", 0) or 0),
        "delta_overlap_rate": round(
            float(current_summary.get("overlap_rate", 0.0) or 0.0)
            - float(previous_summary.get("overlap_rate", 0.0) or 0.0),
            2,
        ),
        "source_family_deltas": family_deltas,
    }


def build_discovery_benchmark_summary(
    discovered_jobs: Sequence[Mapping[str, Any]],
    benchmark_set: DiscoveryBenchmarkSet,
    previous_summary: Mapping[str, Any] | None = None,
    *,
    scope_name: str = "net_new_jobs",
    comparison_scopes: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Build an overlap summary between discovered jobs and the benchmark set."""
    primary_scope_name = str(scope_name).strip() or "net_new_jobs"
    primary_scope_summary = _build_benchmark_scope_summary(
        discovered_jobs,
        benchmark_set,
        include_details=True,
    )
    summary = {
        "benchmark_name": benchmark_set.name,
        "source_report": benchmark_set.source_report,
        "generated_at": datetime.now().isoformat(),
        "scope_name": primary_scope_name,
        "match_strategy": benchmark_set.match_strategy,
        "limitations": [
            "Benchmark overlap now uses canonical URL plus ATS-aware identity aliases when the job shape supports them.",
            "Primary overlap stats default to the state_after_run scope so historically discovered benchmark targets stay counted; the net_new_jobs scope is reported as a comparison.",
            "Broader company-site aliasing without a stable job ID still depends on URL-level matching until later fallback work lands.",
        ],
        "target_count": len(benchmark_set.targets),
        **primary_scope_summary,
    }
    if comparison_scopes:
        summarized_scopes: dict[str, dict[str, Any]] = {}
        for comparison_name, scope_jobs in comparison_scopes.items():
            normalized_name = str(comparison_name).strip()
            if not normalized_name:
                continue
            comparison_summary = _build_benchmark_scope_summary(
                scope_jobs,
                benchmark_set,
                include_details=False,
            )
            comparison_summary["scope_name"] = normalized_name
            summarized_scopes[normalized_name] = comparison_summary
        if summarized_scopes:
            summary["comparison_scopes"] = dict(sorted(summarized_scopes.items()))
    summary["drift"] = _build_benchmark_drift(summary, previous_summary)
    return summary


def compact_discovery_benchmark_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    """Trim a benchmark summary down to the fields worth persisting in state."""
    comparison_scopes = summary.get("comparison_scopes", {})
    return {
        "benchmark_name": str(summary.get("benchmark_name", "")).strip(),
        "source_report": str(summary.get("source_report", "")).strip(),
        "generated_at": str(summary.get("generated_at", "")).strip(),
        "scope_name": str(summary.get("scope_name", "")).strip(),
        "match_strategy": str(summary.get("match_strategy", "normalized_url_only")).strip(),
        "candidate_job_count": int(summary.get("candidate_job_count", 0) or 0),
        "target_count": int(summary.get("target_count", 0) or 0),
        "hit_count": int(summary.get("hit_count", 0) or 0),
        "miss_count": int(summary.get("miss_count", 0) or 0),
        "extra_count": int(summary.get("extra_count", 0) or 0),
        "overlap_rate": float(summary.get("overlap_rate", 0.0) or 0.0),
        "source_family_summary": dict(summary.get("source_family_summary", {})),
        "comparison_scopes": {
            str(scope_name).strip(): {
                "candidate_job_count": int(scope_summary.get("candidate_job_count", 0) or 0),
                "hit_count": int(scope_summary.get("hit_count", 0) or 0),
                "miss_count": int(scope_summary.get("miss_count", 0) or 0),
                "extra_count": int(scope_summary.get("extra_count", 0) or 0),
                "overlap_rate": float(scope_summary.get("overlap_rate", 0.0) or 0.0),
            }
            for scope_name, scope_summary in comparison_scopes.items()
            if str(scope_name).strip() and isinstance(scope_summary, Mapping)
        },
        "misses": list(summary.get("misses", []))[:5],
        "limitations": list(summary.get("limitations", []))[:3],
    }


def find_previous_benchmark_summary(
    state: Mapping[str, Any],
    run_type: str,
) -> dict[str, Any] | None:
    """Return the most recent persisted benchmark summary for the given run type."""
    pipeline_runs = state.get("pipeline_runs", {})
    if not isinstance(pipeline_runs, dict):
        return None

    for run_date in sorted(pipeline_runs.keys(), reverse=True):
        day_runs = pipeline_runs.get(run_date, {})
        if not isinstance(day_runs, dict):
            continue
        run_record = day_runs.get(run_type, {})
        if not isinstance(run_record, dict):
            continue
        benchmark_summary = run_record.get("benchmark_summary")
        if not isinstance(benchmark_summary, dict):
            continue
        previous_summary = dict(benchmark_summary)
        previous_summary.setdefault("record_date", run_date)
        if run_record.get("completed_at"):
            previous_summary.setdefault("completed_at", str(run_record["completed_at"]))
        return previous_summary
    return None