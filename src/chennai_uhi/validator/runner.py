"""Stage 2 — VALIDATOR runner.

Runs ALL checks on every extractor layer. ANY failure → rejected set.
Produces validation_report.json with numeric evidence per check.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any

from chennai_uhi.aoi import load_or_fetch_aoi
from chennai_uhi.config import load_config, month_starts, write_json
from chennai_uhi.validator.checks import (
    check_checksum,
    check_cross_sensor,
    check_crs_resolution,
    check_provenance,
    check_spatial_coverage,
    check_statistical_sanity,
    check_temporal,
)

logger = logging.getLogger(__name__)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _gap_months_from_log(
    fetches: list[dict[str, Any]], required_months: list[str]
) -> dict[str, list[str]]:
    """Per variable, list months with empty composites."""
    gaps: dict[str, list[str]] = {"lst": [], "ndvi": [], "ndbi": []}
    present: dict[str, set[str]] = {"lst": set(), "ndvi": set(), "ndbi": set()}
    for rec in fetches:
        v = rec.get("variable")
        if v not in gaps:
            continue
        month = (rec.get("query_parameters") or {}).get("month")
        if not month:
            continue
        if rec.get("status") in {"empty_month", "empty", "error"} or (
            rec.get("extra") or {}
        ).get("gap_month"):
            gaps[v].append(month)
        else:
            present[v].add(month)
    for v in gaps:
        for m in required_months:
            if m not in present[v] and m not in gaps[v]:
                gaps[v].append(m)
        gaps[v] = sorted(set(gaps[v]))
    return gaps


def run_validator(work_root: Path | None = None) -> dict[str, Any]:
    cfg = load_config(work_root)
    settings = cfg["_settings"]
    allowlist = cfg["_allowlist"]
    paths = cfg["_paths"]
    t_start = date.fromisoformat(cfg["temporal_start"])
    t_end = date.fromisoformat(cfg["temporal_end"])
    threshold = float(settings["coverage"]["min_valid_fraction"])
    target_epsg = int(settings["crs"]["target_epsg"])

    log_path = paths["logs"] / "extractor_fetch_log.json"
    man_path = paths["logs"] / "extractor_manifest.json"
    if not log_path.exists():
        raise FileNotFoundError(f"Run extractor first — missing {log_path}")

    fetch_log = _load_json(log_path)
    manifest = _load_json(man_path) if man_path.exists() else {"layers": []}
    fetches = fetch_log.get("fetches", [])

    aoi, _ = load_or_fetch_aoi(paths["aoi"], target_epsg=target_epsg)
    required_months = [d.strftime("%Y-%m") for d in month_starts(t_start, t_end)]
    gap_months = _gap_months_from_log(fetches, required_months)
    layer_by_id = {ly["layer_id"]: ly for ly in manifest.get("layers", [])}

    rejected_dir = paths["rejected"]
    rejected_dir.mkdir(parents=True, exist_ok=True)

    layer_reports: list[dict[str, Any]] = []
    passed: list[str] = []
    rejected: list[dict[str, Any]] = []
    skip_clean = {"landsat_stac_search"}

    for rec in fetches:
        layer_id = rec["layer_id"]
        variable = rec.get("variable", "")
        path = rec.get("local_path") or (layer_by_id.get(layer_id) or {}).get("path")

        checks = [
            check_provenance(rec, allowlist),
            check_checksum(rec),
            check_temporal(rec, t_start, t_end, gap_months=gap_months.get(variable)),
            check_spatial_coverage(path, None, aoi, threshold),
            check_crs_resolution(path, rec, target_epsg),
            check_cross_sensor(rec),
            check_statistical_sanity(path, variable),
        ]

        failures = [c for c in checks if c["verdict"] == "FAIL"]
        if rec.get("status") == "excluded_by_design":
            overall = "REJECTED"
            failures.append(
                {
                    "check": "design_exclusion",
                    "verdict": "FAIL",
                    "reason": rec.get("notes"),
                    "evidence": {"status": "excluded_by_design"},
                }
            )
        elif variable in skip_clean:
            overall = "INFO"
        elif rec.get("status") == "error":
            overall = "REJECTED"
            failures.append(
                {
                    "check": "extractor_status",
                    "verdict": "FAIL",
                    "reason": rec.get("error") or "extractor_error",
                    "evidence": {},
                }
            )
        else:
            overall = "PASSED" if not failures else "REJECTED"

        entry = {
            "layer_id": layer_id,
            "variable": variable,
            "local_path": path,
            "overall": overall,
            "checks": checks,
            "failure_reasons": [f.get("reason") for f in failures],
            "fetch_record_ref": {
                "source_name": rec.get("source_name"),
                "resolved_source_url": rec.get("resolved_source_url"),
                "data_dates": rec.get("data_dates"),
                "last_available_date": rec.get("last_available_date"),
            },
        }
        layer_reports.append(entry)

        if overall == "REJECTED":
            rejected.append(entry)
            write_json(rejected_dir / f"{layer_id}_rejected.json", entry)
            if path and Path(path).exists() and Path(path).suffix.lower() in {
                ".tif",
                ".tiff",
                ".gpkg",
            }:
                dest = rejected_dir / Path(path).name
                if not dest.exists():
                    try:
                        shutil.copy2(path, dest)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Could not copy rejected file: %s", exc)
        elif overall == "PASSED" and variable not in skip_clean:
            passed.append(layer_id)

    series_gaps = {
        "required_months": required_months,
        "gap_months_by_variable": gap_months,
        "note": "Each gap month listed individually — not averaged over the series",
    }

    report = {
        "stage": "validator",
        "temporal_start": cfg["temporal_start"],
        "temporal_end": cfg["temporal_end"],
        "coverage_threshold": threshold,
        "target_epsg": target_epsg,
        "n_layers_evaluated": len(layer_reports),
        "n_passed": len(passed),
        "n_rejected": len(rejected),
        "passed_layer_ids": passed,
        "rejected_layer_ids": [r["layer_id"] for r in rejected],
        "series_temporal_gaps": series_gaps,
        "design_exclusions": {
            "building_height": (
                "Excluded by design: <5% real OSM height-tag coverage; "
                "Microsoft building-height placeholders for this region. "
                "Do not reconstruct H/W."
            ),
            "height_width_ratio": "Excluded by design (depends on building height).",
        },
        "layers": layer_reports,
        "extractor_fetch_log": str(log_path),
    }
    out = paths["logs"] / "validation_report.json"
    write_json(out, report)

    passed_manifest = {
        "stage": "validator_passed",
        "validation_report": str(out),
        "reference_grid": str(paths["aoi"] / "reference_grid.json"),
        "layers": [
            {**layer_by_id[lid], "validation_entry_layer_id": lid}
            for lid in passed
            if lid in layer_by_id
        ],
    }
    write_json(paths["logs"] / "validator_passed_manifest.json", passed_manifest)
    logger.info("VALIDATOR done — %d passed, %d rejected", len(passed), len(rejected))
    return report
