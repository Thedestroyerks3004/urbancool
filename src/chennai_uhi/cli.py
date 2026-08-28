"""CLI: run extractor → validator → cleaner in sequence."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chennai-uhi",
        description=(
            "Chennai UHI three-stage geospatial pipeline "
            "(EXTRACTOR → VALIDATOR → CLEANER). "
            "Only CLEANER outputs for PASSED layers are analysis-ready."
        ),
    )
    parser.add_argument(
        "stage",
        choices=["extractor", "validator", "standalone-validator", "cleaner", "all"],
        help="Pipeline stage to run",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=None,
        help="Pipeline root (contains config/ and data/). Default: package root.",
    )
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="Artifact directory for standalone-validator (default: data/raw).",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Ensure src on path when run as script
    root = args.work_root
    if root is None:
        root = Path(__file__).resolve().parents[2]
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    if args.stage in {"extractor", "all"}:
        from chennai_uhi.extractor import run_extractor

        run_extractor(root)
    if args.stage in {"validator", "all"}:
        from chennai_uhi.validator import run_validator

        run_validator(root)
    if args.stage == "standalone-validator":
        from chennai_uhi.validator import run_standalone_validator

        report_name = "standalone_validation_report_clean.json" if args.artifact_dir else "standalone_validation_report.json"
        run_standalone_validator(root, artifact_dir=args.artifact_dir, report_name=report_name)
    if args.stage in {"cleaner", "all"}:
        from chennai_uhi.cleaner import run_cleaner

        run_cleaner(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
