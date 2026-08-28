"""Standalone validation of extractor artifacts without running the extractor."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np

from chennai_uhi.config import load_config, month_starts, write_json

EXPECTED = {
    "lst": "raster_monthly", "ndvi": "raster_monthly", "ndbi": "raster_monthly",
    "building_footprints": "vector_snapshot", "road_network": "vector_snapshot",
    "building_density": "raster_static",
    "air_temperature": "timeseries", "dem": "raster_static", "slope": "raster_static",
    "population_density": "raster_annual",
}
RANGES = {
    "lst": (280.0, 340.0), "ndvi": (-1.0, 1.0), "ndbi": (-1.0, 1.0),
    "dem": (-50.0, 500.0), "slope": (0.0, 90.0), "population_density": (0.0, 1_000_000.0),
}


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _variable(path: Path) -> str | None:
    name = path.name.lower()
    for value in EXPECTED:
        if value in name:
            return value
    if "airtemp" in name or "air_temp" in name:
        return "air_temperature"
    if "building" in name and path.suffix.lower() == ".gpkg":
        return "building_footprints"
    if "road" in name and path.suffix.lower() == ".gpkg":
        return "road_network"
    return None


def _month(path: Path) -> str | None:
    match = re.search(r"(20\d\d-\d\d)", path.name)
    return match.group(1) if match else None


def _record_metadata(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    log = _load(root / "data/logs/extractor_fetch_log.json", {})
    for record in log.get("fetches", []):
        if record.get("local_path"):
            result[Path(record["local_path"]).name] = record
    return result


def _check(name: str, verdict: str, reason: str | None, **evidence: Any) -> dict[str, Any]:
    return {"check": name, "verdict": verdict, "reason": reason, "evidence": evidence}


def _path_health(path: Path, readable: bool) -> dict[str, Any]:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    stat = path.stat()
    return {
        "exists": True,
        "readable": readable,
        "suffix": path.suffix.lower(),
        "size_bytes": stat.st_size,
        "size_mb": round(stat.st_size / (1024 * 1024), 3),
        "modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(),
        "sha256": digest.hexdigest(),
    }


def _raster_checks(path: Path, variable: str, aoi: Any, target_epsg: int, threshold: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import rasterio
    from rasterio import features

    with rasterio.open(path) as src:
        data = src.read(1, masked=True)
        valid = (~np.asarray(data.mask)) & np.isfinite(np.asarray(data.data))
        structural = src.count == 1 and src.crs is not None and all(v > 0 for v in src.res)
        checks = [_check("structure", "PASS" if structural else "FAIL", None if structural else "missing_crs_resolution_or_expected_band_count", bands=src.count, crs=str(src.crs), resolution=list(src.res), width=src.width, height=src.height)]
        epsg = src.crs.to_epsg() if src.crs else None
        resolution_m = abs(src.res[0]) * 111320 if src.crs and src.crs.is_geographic else abs(src.res[0])
        matching = epsg == target_epsg and resolution_m <= 30.0 + 1e-6
        checks.append(_check("crs_resolution", "PASS" if matching else "FAIL", None if matching else "crs_or_resolution_mismatch", crs_found=str(src.crs), crs_expected=f"EPSG:{target_epsg}", resolution_m=resolution_m, max_resolution_m=30.0))
        aoi_r = aoi.to_crs(src.crs)
        geoms = [g.__geo_interface__ for g in aoi_r.geometry if g is not None and not g.is_empty]
        mask = features.geometry_mask(geoms, out_shape=data.shape, transform=src.transform, invert=True)
        inside = int(mask.sum())
        valid_inside = mask & valid
        fraction = float(valid_inside.sum() / inside) if inside else 0.0
        rows, cols = np.where(mask & ~valid)
        gap_bbox = {"row_min": int(rows.min()), "row_max": int(rows.max()), "col_min": int(cols.min()), "col_max": int(cols.max())} if len(rows) else None
        covered = inside > 0 and fraction >= threshold
        checks.append(_check("spatial_coverage", "PASS" if covered else "FAIL", None if covered else "coverage_below_threshold", coverage_pct=round(fraction * 100, 3), valid_pixels=int(valid_inside.sum()), aoi_pixels=inside, threshold_pct=threshold * 100, gap_pixel_bbox=gap_bbox))
        if variable in RANGES:
            values = np.asarray(data.data[valid], dtype="float64")
            lo, hi = RANGES[variable]
            if values.size:
                outside = (values < lo) | (values > hi)
                sane = not bool(outside.any())
                checks.append(_check("statistical_sanity", "PASS" if sane else "FAIL", None if sane else "values_outside_plausible_range", expected_range=[lo, hi], min=float(values.min()), max=float(values.max()), mean=float(values.mean()), fraction_out_of_range=round(float(outside.mean()), 6)))
            else:
                checks.append(_check("statistical_sanity", "FAIL", "no_valid_pixels", expected_range=[lo, hi]))
        return checks, {"shape": [src.height, src.width], "transform": list(src.transform)[:6], "crs": str(src.crs), "resolution_m": resolution_m, "coverage_pct": round(fraction * 100, 3)}


def _vector_checks(path: Path, target_epsg: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import geopandas as gpd

    try:
        layers = gpd.list_layers(path)
        layer_name = str(layers.iloc[0]["name"]) if len(layers) else None
        gdf = gpd.read_file(path, layer=layer_name) if layer_name else gpd.GeoDataFrame()
        invalid = int((~gdf.geometry.is_valid).sum()) if len(gdf) else 0
        empty = int(gdf.geometry.isna().sum() + gdf.geometry.is_empty.sum()) if len(gdf) else 0
        duplicates = int(gdf.geometry.duplicated().sum()) if len(gdf) else 0
        structural = len(gdf) > 0 and invalid == 0 and empty == 0
        checks = [_check("structure", "PASS" if structural else "FAIL", None if structural else "empty_or_invalid_geometries", feature_count=len(gdf), invalid_geometry_count=invalid, empty_geometry_count=empty, duplicate_geometry_count=duplicates, layers=list(layers.name))]
        epsg = gdf.crs.to_epsg() if gdf.crs else None
        matching = epsg == target_epsg
        checks.append(_check("crs_resolution", "PASS" if matching else "FAIL", None if matching else "crs_mismatch", crs_found=str(gdf.crs), crs_expected=f"EPSG:{target_epsg}"))
        return checks, {"feature_count": len(gdf), "crs": str(gdf.crs), "bounds": list(gdf.total_bounds) if len(gdf) else None}
    except Exception as exc:
        return [_check("structure", "FAIL", "vector_read_error", error=str(exc))], {}


def _verdict(checks: list[dict[str, Any]], provenance_known: bool) -> tuple[str, list[str]]:
    failures = [c["reason"] for c in checks if c["verdict"] == "FAIL" and c.get("reason")]
    caveats = [] if provenance_known else ["provenance_unverifiable"]
    if failures:
        return "REJECT", failures + caveats
    if caveats or any(c["verdict"] == "SKIPPED" for c in checks):
        return "USABLE WITH CAVEATS", caveats + [c["reason"] for c in checks if c["verdict"] == "SKIPPED"]
    return "USABLE", []


def _cross_layer(entries: list[dict[str, Any]], allowlist: dict[str, list[str]]) -> dict[str, Any]:
    import rasterio

    pairs: list[dict[str, Any]] = []
    density = next((e for e in entries if e["layer_name"] == "building_density"), None)
    ndbi = next((e for e in entries if e["layer_name"] == "ndbi" and e.get("overall_verdict") != "REJECT"), None)
    if density and ndbi:
        with rasterio.open(density["file_path"]) as left, rasterio.open(ndbi["file_path"]) as right:
            compatible = left.shape == right.shape and left.transform == right.transform and left.crs == right.crs
            evidence: dict[str, Any] = {"left": density["file_path"], "right": ndbi["file_path"], "same_grid": compatible, "overlap_fraction": 0.0}
            if compatible:
                a = left.read(1).astype(float)
                b = right.read(1).astype(float)
                valid = np.isfinite(a) & np.isfinite(b)
                evidence["overlap_fraction"] = round(float(valid.mean()), 6)
                if valid.sum() > 2:
                    evidence["pearson_correlation"] = round(float(np.corrcoef(a[valid], b[valid])[0, 1]), 6)
            pairs.append({"pair": ["building_density", "ndbi"], "status": "PASS" if compatible else "FAIL", "evidence": evidence})
    return {"status": "PASS" if pairs else "SKIPPED", "pairs": pairs, "reason": None if pairs else "no_compatible_building_density_and_ndbi_rasters"}


def run_standalone_validator(
    work_root: Path | None = None,
    artifact_dir: Path | None = None,
    report_name: str = "standalone_validation_report.json",
) -> dict[str, Any]:
    """Discover and validate raw artifacts; never downloads or changes data."""
    cfg = load_config(work_root)
    root = Path(work_root or cfg["_paths"]["root"].parent)
    raw = artifact_dir or cfg["_paths"]["raw"]
    import geopandas as gpd

    target_epsg = int(cfg["_settings"]["crs"]["target_epsg"])
    threshold = float(cfg["_settings"]["coverage"]["min_valid_fraction"])
    allowlist = cfg["_allowlist"]
    aoi = gpd.read_file(cfg["_paths"]["aoi"] / "chennai_boundary.geojson").to_crs(target_epsg)
    metadata = _record_metadata(root)
    required_months = {d.strftime("%Y-%m") for d in month_starts(date.fromisoformat(cfg["temporal_start"]), date.fromisoformat(cfg["temporal_end"]))}
    entries: list[dict[str, Any]] = []
    for path in sorted(raw.rglob("*")):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        if path.name.endswith("_meta.json") or path.name == "CLEAN_DATASET_SUMMARY.json":
            continue
        variable = _variable(path)
        if not variable:
            continue
        if path.suffix.lower() in {".tif", ".tiff"}:
            checks, evidence = _raster_checks(path, variable, aoi, target_epsg, threshold)
        elif path.suffix.lower() in {".gpkg", ".geojson"}:
            checks, evidence = _vector_checks(path, target_epsg)
        else:
            checks, evidence = [_check("structure", "SKIPPED", "format_not_supported_for_deep_inspection", suffix=path.suffix)], {}
        readable = not any(c["check"] == "structure" and c["verdict"] == "FAIL" for c in checks)
        path_health = _path_health(path, readable)
        record = metadata.get(path.name, {})
        provenance_known = bool(record.get("resolved_source_url"))
        source_url = record.get("resolved_source_url")
        source_host = record.get("resolved_host") or (urlparse(source_url).netloc.lower() if source_url else None)
        source_key = (
            "landsat" if variable in {"lst", "ndvi", "ndbi"}
            else "osm" if variable in {"building_footprints", "road_network"}
            else "weather" if variable == "air_temperature"
            else "population" if variable == "population_density"
            else "dem" if variable == "slope"
            else "osm" if variable == "building_density"
            else variable
        )
        allowed_hosts = allowlist.get(source_key, [])
        host_ok = bool(source_host and any(source_host == h or source_host.endswith("." + h) for h in allowed_hosts))
        provenance_verdict = "SKIPPED" if not provenance_known else "PASS" if host_ok else "FAIL"
        provenance_reason = "no_source_url_or_sidecar_metadata" if not provenance_known else None if host_ok else "host_not_allowlisted"
        checks.append(_check("provenance", provenance_verdict, provenance_reason, source_url=source_url, source_host=source_host, allowlist_key=source_key, allowed_hosts=allowed_hosts))
        checks.append(_check("checksum", "PASS" if record.get("checksum_md5_source") and record.get("checksum_md5_computed") == record.get("checksum_md5_source") else "SKIPPED", "published_checksum_unavailable" if not record.get("checksum_md5_source") else None, source_md5=record.get("checksum_md5_source"), computed_md5=record.get("checksum_md5_computed"), local_sha256=record.get("checksum_sha256")))
        month = _month(path)
        if variable in {"lst", "ndvi", "ndbi"}:
            checks.append(_check("temporal_coverage", "PASS" if month in required_months else "FAIL", None if month in required_months else "month_not_in_required_window", file_month=month, required_start=cfg["temporal_start"], required_end=cfg["temporal_end"]))
        else:
            checks.append(_check("temporal_coverage", "SKIPPED", "static_or_snapshot_requires_fetch_metadata", required_start=cfg["temporal_start"], required_end=cfg["temporal_end"]))
        verdict, caveats = _verdict(checks, provenance_known)
        inferred = {"variable": variable, "format": path.suffix.lower()}
        if month:
            inferred["month"] = month
        entries.append({"layer_name": variable, "file_path": str(path), "path_health": path_health, "extraction": {"source_name": record.get("source_name"), "source_url": source_url, "fetch_timestamp": record.get("fetch_timestamp"), "status": record.get("status"), "parameters": record.get("query_parameters") or inferred, "parameters_source": "extractor_log" if record.get("query_parameters") else "filename_inference", "data_dates": record.get("data_dates") or [], "last_available_date": record.get("last_available_date")}, "checks": checks, "evidence": evidence, "overall_verdict": verdict, "caveats": caveats})
    for variable in EXPECTED:
        found = [e for e in entries if e["layer_name"] == variable]
        if variable in {"lst", "ndvi", "ndbi"}:
            present = {_month(Path(e["file_path"])) for e in found}
            for missing in sorted(required_months - present):
                entries.append({"layer_name": variable, "file_path": None, "path_health": {"exists": False, "readable": False}, "extraction": {"parameters": {}, "data_dates": []}, "checks": [_check("temporal_coverage", "FAIL", "missing_month", month=missing)], "evidence": {}, "overall_verdict": "REJECT", "caveats": [f"missing_month:{missing}"]})
        elif not found:
            entries.append({"layer_name": variable, "file_path": None, "path_health": {"exists": False, "readable": False}, "extraction": {"parameters": {}, "data_dates": []}, "checks": [_check("structure", "FAIL", "expected_layer_missing")], "evidence": {}, "overall_verdict": "REJECT", "caveats": ["expected_layer_missing"]})
    datasets: dict[str, dict[str, Any]] = {}
    for variable in EXPECTED:
        group = [e for e in entries if e["layer_name"] == variable]
        observed_months = sorted({_month(Path(e["file_path"])) for e in group if e.get("file_path") and _month(Path(e["file_path"]))})
        observed_dates = sorted({d for e in group for d in e.get("extraction", {}).get("data_dates", [])})
        datasets[variable] = {"kind": EXPECTED[variable], "file_count": sum(bool(e.get("file_path")) for e in group), "files": [e.get("file_path") for e in group if e.get("file_path")], "expected_months": sorted(required_months) if variable in {"lst", "ndvi", "ndbi"} else [], "observed_months": observed_months, "missing_months": sorted(required_months - set(observed_months)) if variable in {"lst", "ndvi", "ndbi"} else [], "observed_data_dates": observed_dates, "fetch_timestamps": sorted({e.get("extraction", {}).get("fetch_timestamp") for e in group if e.get("extraction", {}).get("fetch_timestamp")}), "parameters": [e.get("extraction", {}).get("parameters", {}) for e in group if e.get("file_path")], "status_counts": {status: sum(e.get("extraction", {}).get("status") == status for e in group) for status in {e.get("extraction", {}).get("status") for e in group if e.get("extraction", {}).get("status")}}}
    buckets = {"correct_matching_healthy": [e["file_path"] for e in entries if e["overall_verdict"] == "USABLE"], "usable_with_caveats": [e["file_path"] for e in entries if e["overall_verdict"] == "USABLE WITH CAVEATS"], "rejected": [e["file_path"] for e in entries if e["overall_verdict"] == "REJECT"]}
    report = {"stage": "standalone_validator", "generated_at": date.today().isoformat(), "assumptions": {"target_crs": f"EPSG:{target_epsg}", "max_resolution_m": 30, "coverage_threshold_pct": threshold * 100, "date_range": [cfg["temporal_start"], cfg["temporal_end"]], "raw_directory": str(raw), "provenance": "extractor log or sidecar; absent metadata is skipped and becomes a caveat"}, "n_entries": len(entries), "datasets": datasets, "entries": entries, "buckets": buckets, "cross_layer": _cross_layer(entries, allowlist)}
    write_json(cfg["_paths"]["logs"] / report_name, report)
    lines = ["# Standalone Validator Report", "", f"- Entries: {len(entries)}", f"- Target CRS: EPSG:{target_epsg}", f"- Coverage threshold: {threshold:.0%}", "", "## Dataset Timelines", "", "| Dataset | Files | Observed periods | Missing periods |", "|---|---:|---|---|"]
    for variable, dataset in datasets.items():
        lines.append(f"| `{variable}` | {dataset['file_count']} | {', '.join(dataset['observed_months']) or ', '.join(dataset['observed_data_dates']) or 'none'} | {', '.join(dataset['missing_months']) or 'none'} |")
    lines.extend(["", "## Buckets", ""])
    for bucket, paths in buckets.items():
        lines.append(f"### {bucket} ({len(paths)})")
        lines.extend(f"- `{p or 'missing'}`" for p in paths)
        lines.append("")
    report_stem = Path(report_name).stem
    (cfg["_paths"]["logs"] / f"{report_stem}.md").write_text("\n".join(lines), encoding="utf-8")
    return report