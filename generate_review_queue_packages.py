#!/usr/bin/env python3
"""Generate tailored application packs directly from the review-queue CSV export."""

from __future__ import annotations

import argparse
from pathlib import Path

from generate_application_package import (
    DEFAULT_REVIEW_PACKAGE_ROOT,
    generate_review_csv_applications,
    parse_queue_ranks,
)
from utils.runtime_paths import regular_jobs_csv_path


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments for review-queue package generation."""
    parser = argparse.ArgumentParser(
        description="Generate tailored resumes and cover letters from reports/regular_jobs_export.csv"
    )
    parser.add_argument(
        "--review-csv",
        default=str(regular_jobs_csv_path()),
        help="Path to the review CSV exported by hot_job_processor.py",
    )
    parser.add_argument(
        "--queue-ranks",
        help="Comma-separated Queue Rank values from the review CSV to process",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of review rows to process after filtering",
    )
    parser.add_argument(
        "--include-non-actionable-review",
        action="store_true",
        help="Include rows that are not marked Actionable Review=yes",
    )
    parser.add_argument(
        "--package-root",
        help=(
            "Override the output folder. Defaults to "
            f"{DEFAULT_REVIEW_PACKAGE_ROOT.as_posix()}"
        ),
    )
    parser.add_argument(
        "--include-supporting-artifacts",
        action="store_true",
        help="Keep analysis, source markdown, and review checklist files in each package",
    )
    parser.add_argument("--model", help="Override the OpenAI model used for generation")
    parser.add_argument(
        "--min-match-score",
        type=int,
        help="Only process review rows at or above this historical match score",
    )
    parser.add_argument(
        "--max-match-score",
        type=int,
        help="Only process review rows at or below this historical match score",
    )
    return parser.parse_args()


def main() -> int:
    """Run the review-queue package generator."""
    args = parse_arguments()

    try:
        results = generate_review_csv_applications(
            Path(args.review_csv),
            queue_ranks=parse_queue_ranks(args.queue_ranks),
            package_root_override=(Path(args.package_root) if args.package_root else DEFAULT_REVIEW_PACKAGE_ROOT),
            model_override=args.model,
            include_supporting_artifacts_override=(
                True if args.include_supporting_artifacts else None
            ),
            min_match_score=args.min_match_score,
            max_match_score=args.max_match_score,
            actionable_only=not args.include_non_actionable_review,
            limit=args.limit,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}")
        return 1

    print("\n=== REVIEW QUEUE RESULTS ===")
    for result in results:
        print(result)
    return 0 if all(result.get("status") == "success" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())