"""Build the locked, time-aligned Stage 3 modeling manifest."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import rasterio


ROOT = Path(__file__).resolve().parents[1]
CLEAN = ROOT / "data" / "clean"
LOGS = ROOT / "data" / "logs"
SUMMARY_PATH = CLEAN / "CLEAN_DATASET_SUMMARY.json"
MANIFEST_PATH = LOGS / "MODELING_READY_MANIFEST.json"
REPORT_PATH = LOGS / "MODELING_READY_REPORT.md"
VARIABLES = ("lst", "ndvi", "ndbi")
EXPECTED_GRID = {
    "width": 495,
    "height": 671,
    "resolution_m": 30.0,
    "epsg": 32644,
    "origin_x": 409950.0,
    "origin_y": 1452060.0,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    summary = load_json(SUMMARY_PATH)
    outputs = summary["outputs"]
    by_month: dict[str, dict[str, dict[str, Any]]] = {v: {} for v in VARIABLES}
    static: dict[str, dict[str, Any]] = {}
    weather: dict[str, Any] | None = None
    for output in outputs:
        variable = output.get("variable")
        path = Path(output["clean_path"])
        if variable in VARIABLES and output.get("month"):
            by_month[variable][output["month"]] = output
        elif variable == "air_temperature":
            weather = output
        elif variable and path.exists():
            static[variable] = output

    month_sets = {v: set(by_month[v]) for v in VARIABLES}
    common_months = sorted(set.intersection(*(month_sets[v] for v in VARIABLES)))
    start = date.fromisoformat(summary["temporal_start"])
    end = date.fromisoformat(summary["temporal_end"])
    required_months = []
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        required_months.append(cursor.strftime("%Y-%m"))
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)

    excluded: list[dict[str, Any]] = []
    exclusions = summary.get("excluded_from_clean", [])
    for month in required_months:
        if month in common_months:
            continue
        reasons = {}
        for variable in VARIABLES:
            output = by_month[variable].get(month)
            if output:
                reasons[variable] = "present_individually_but_excluded_from_common_set"
            else:
                matches = [
                    item for item in exclusions
                    if item.get("variable") == variable and month in item.get("layer_id", "")
                ]
                reasons[variable] = (matches[0].get("reasons") if matches else ["missing_from_clean_outputs"])
        excluded.append({"month": month, "per_layer_reasons": reasons})

    if weather is None:
        raise RuntimeError("Clean weather output is missing")
    weather_path = Path(weather["clean_path"])
    weather_payload = load_json(weather_path)
    weather_dates = set(weather_payload.get("daily", {}).get("time", []))
    common_scene_dates = set()
    for month in common_months:
        dates = set()
        for variable in VARIABLES:
            sidecar = load_json(CLEAN / f"{Path(by_month[variable][month]['clean_path']).stem}_meta.json")
            dates.update(sidecar.get("original_acquisition_dates") or [])
        common_scene_dates.update(dates)
    missing_weather_dates = sorted(common_scene_dates - weather_dates)
    if missing_weather_dates:
        raise RuntimeError(f"Weather dates missing from clean output: {missing_weather_dates}")

    grid = summary["reference_grid"]
    raster_checks = []
    for output in outputs:
        path = Path(output["clean_path"])
        if path.suffix.lower() not in {".tif", ".tiff"}:
            continue
        with rasterio.open(path) as src:
            observed = {
                "width": src.width,
                "height": src.height,
                "resolution_m": float(src.res[0]),
                "epsg": src.crs.to_epsg() if src.crs else None,
                "origin_x": float(src.transform.c),
                "origin_y": float(src.transform.f),
            }
        matches = observed == EXPECTED_GRID
        raster_checks.append({"path": str(path), "matches_reference_grid": matches, "grid": observed, "sha256": sha256(path)})
        if not matches:
            raise RuntimeError(f"Grid mismatch: {path}: {observed}")

    def file_record(output: dict[str, Any]) -> dict[str, Any]:
        path = Path(output["clean_path"])
        return {
            "path": str(path),
            "exists": path.exists(),
            "sha256": sha256(path),
            "sidecar": str(CLEAN / f"{path.stem}_meta.json") if path.suffix.lower() == ".tif" else None,
        }

    monthly = {
        month: {variable: file_record(by_month[variable][month]) for variable in VARIABLES}
        for month in common_months
    }
    static_paths = {variable: file_record(output) for variable, output in sorted(static.items())}
    population_sidecar = next(
        (load_json(CLEAN / f"{Path(output['clean_path']).stem}_meta.json") for variable, output in static.items() if variable == "population_density"),
        {},
    )
    manifest = {
        "manifest_type": "chennai_uhi_modeling_ready",
        "manifest_version": 1,
        "locked_at": date.today().isoformat(),
        "stage4_started": False,
        "source_clean_summary": str(SUMMARY_PATH),
        "common_healthy_months": common_months,
        "modeling_month_count": len(common_months),
        "excluded_months": excluded,
        "monthly_layers": monthly,
        "static_layers": static_paths,
        "weather": {
            "path": str(weather_path),
            "exists": weather_path.exists(),
            "sha256": sha256(weather_path),
            "record_count": len(weather_payload.get("daily", {}).get("time", [])),
            "scene_date_count_for_common_months": len(common_scene_dates),
            "missing_scene_dates": missing_weather_dates,
            "exact_date_match": not missing_weather_dates,
        },
        "population": {
            "path": static_paths.get("population_density", {}).get("path"),
            "fallback": population_sidecar.get("fallback", False),
            "population_source_type": population_sidecar.get("population_source_type"),
        },
        "grid": {**EXPECTED_GRID, "reference_grid": summary["reference_grid"]},
        "checks": {
            "all_manifest_files_exist": all(
                record["exists"]
                for group in [monthly, static_paths]
                for item in (group.values() if isinstance(group, dict) else [])
                for record in (item.values() if isinstance(item, dict) and "path" not in item else [item])
            ) and weather_path.exists(),
            "all_referenced_rasters_match_grid": all(item["matches_reference_grid"] for item in raster_checks),
            "raster_count_checked": len(raster_checks),
            "weather_dates_complete": not missing_weather_dates,
        },
        "raster_checks": raster_checks,
        "counts": {
            "common_months": len(common_months),
            "excluded_months": len(excluded),
            "monthly_raster_references": len(common_months) * len(VARIABLES),
            "static_layer_references": len(static_paths),
        },
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# Modeling-Ready Dataset Report",
        "",
        f"**Locked manifest:** `{MANIFEST_PATH.name}`  ",
        f"**Modeling months:** {len(common_months)}  ",
        f"**Excluded months:** {len(excluded)}  ",
        "**Stage 4 feature engineering:** Not performed",
        "",
        "## Common Healthy Months",
        "",
        "```python",
        f"MODELING_MONTHS = {common_months!r}",
        "```",
        "",
        "The month set is the intersection of clean LST, NDVI, and NDBI months. A month is included only when all three layers are present.",
        "",
        "## Excluded Months",
        "",
    ]
    for item in excluded:
        lines.append(f"- `{item['month']}`: " + "; ".join(f"{v}={r if isinstance(r, str) else ', '.join(r)}" for v, r in item["per_layer_reasons"].items()))
    lines.extend([
        "",
        "## Alignment and Integrity",
        "",
        f"- Weather dates required for common-month Landsat scenes: `{len(common_scene_dates)}`.",
        f"- Missing weather dates: `{len(missing_weather_dates)}`.",
        f"- Clean rasters checked: `{len(raster_checks)}`; grid mismatches: `0`.",
        f"- Grid: `495 x 671`, `30 m`, `EPSG:32644`, origin `E409950/N1452060`.",
        f"- Population source type: `{population_sidecar.get('population_source_type')}`; fallback: `{population_sidecar.get('fallback', False)}`.",
        "",
        "## Readiness",
        "",
        "The manifest is the single source of truth for the healthy, time-aligned Stage 3 modeling inputs. It is sufficient to begin Stage 4 feature engineering, but this task did not begin Stage 4 and performed no feature engineering, model training, or per-cell aggregation.",
    ])
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST_PATH}")
    print(f"wrote {REPORT_PATH}")
    print(f"common_months={len(common_months)} excluded_months={len(excluded)} raster_checks={len(raster_checks)}")


if __name__ == "__main__":
    main()
