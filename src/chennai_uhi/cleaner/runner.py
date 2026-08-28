"""Stage 3 — CLEANER.

Only layers that PASSED every Stage 2 check.
Reproject → shared grid → AOI polygon clip → conservative gap fill → named outputs + sidecars.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from chennai_uhi.aoi import load_or_fetch_aoi
from chennai_uhi.config import load_config, write_json
from chennai_uhi.cleaner.gap_fill import fill_small_gaps
from chennai_uhi.cleaner.grid_align import aoi_mask_for_grid, apply_aoi_mask, warp_to_grid
from chennai_uhi.extractor.base import write_geotiff
from chennai_uhi.grid import ReferenceGrid
from chennai_uhi.logging_util import sha256_file, utc_now_iso

logger = logging.getLogger(__name__)

UNIT_MAP = {
    "lst": "k",
    "ndvi": "index",
    "ndbi": "index",
    "dem": "m",
    "slope": "deg",
    "landcover": "class",
    "population_density": "people",
    "building_density": "fraction",
    "building_compactness": "index",
    "street_width": "m",
    "air_temperature": "celsius",
}


def _clean_name(variable: str, unit: str, yyyymm: str | None, res_m: float, ext: str = "tif") -> str:
    res = int(res_m) if float(res_m).is_integer() else res_m
    if yyyymm:
        return f"chennai_{variable}_{unit}_{yyyymm}_{res}m.{ext}"
    return f"chennai_{variable}_{unit}_static_{res}m.{ext}"


def run_cleaner(work_root: Path | None = None) -> dict[str, Any]:
    cfg = load_config(work_root)
    settings = cfg["_settings"]
    paths = cfg["_paths"]
    target_epsg = int(settings["crs"]["target_epsg"])
    gap_cfg = settings.get("gap_fill", {})
    max_radius = int(gap_cfg.get("max_radius_px", 3))

    passed_path = paths["logs"] / "validator_passed_manifest.json"
    report_path = paths["logs"] / "validation_report.json"
    if not passed_path.exists():
        raise FileNotFoundError("Run validator first — missing validator_passed_manifest.json")

    passed_man = json.loads(passed_path.read_text(encoding="utf-8"))
    val_report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    fetch_log_path = paths["logs"] / "extractor_fetch_log.json"
    fetch_records = {}
    if fetch_log_path.exists():
        fetch_log = json.loads(fetch_log_path.read_text(encoding="utf-8"))
        fetch_records = {record.get("layer_id"): record for record in fetch_log.get("fetches", [])}

    grid_path = Path(passed_man.get("reference_grid") or (paths["aoi"] / "reference_grid.json"))
    grid = ReferenceGrid.load(grid_path)
    if grid.epsg != target_epsg:
        raise RuntimeError(f"Reference grid EPSG {grid.epsg} != target {target_epsg}")
    if grid.cell_size_m > 30:
        raise RuntimeError("Reference grid cell size must be ≤ 30 m")

    aoi, _ = load_or_fetch_aoi(paths["aoi"], target_epsg=target_epsg)
    mask = aoi_mask_for_grid(aoi, grid)

    clean_dir = paths["clean"]
    clean_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[dict[str, Any]] = []
    transforms: list[str] = []
    shapes: list[tuple[int, int]] = []

    for layer in passed_man.get("layers", []):
        variable = layer.get("variable")
        src = layer.get("path")
        layer_id = layer.get("layer_id")
        if not src or not Path(src).exists():
            logger.warning("Skipping missing passed layer %s", layer_id)
            continue

        # Non-raster: copy JSON timeseries with sidecar
        if layer.get("data_type") == "timeseries" or Path(src).suffix.lower() == ".json":
            unit = UNIT_MAP.get(variable, layer.get("unit", "na"))
            name = f"chennai_{variable}_{unit}_acqdates.json"
            dest = clean_dir / name
            shutil.copy2(src, dest)
            sidecar = {
                "layer_id": layer_id,
                "variable": variable,
                "unit": unit,
                "source_path": src,
                "clean_path": str(dest),
                "crs": None,
                "resolution_m": None,
                "gap_fill": {"method": "none"},
                "validation_report": str(report_path),
                "validation_entry_layer_id": layer_id,
                "cleaned_at": utc_now_iso(),
            }
            record = fetch_records.get(layer_id, {})
            if record.get("extra", {}).get("population_source_type"):
                sidecar["population_source_type"] = record["extra"]["population_source_type"]
                sidecar["fallback"] = record["extra"]["population_source_type"] == "synthetic_fallback"
            write_json(clean_dir / f"{Path(name).stem}_meta.json", sidecar)
            outputs.append(sidecar)
            continue

        if Path(src).suffix.lower() not in {".tif", ".tiff"}:
            # Vector GPKG — clip & reproject copy
            if Path(src).suffix.lower() == ".gpkg":
                import geopandas as gpd

                gdf = gpd.read_file(src)
                if gdf.crs is None:
                    gdf = gdf.set_crs(epsg=4326)
                gdf = gdf.to_crs(epsg=target_epsg)
                gdf = gpd.clip(gdf, aoi)
                unit = UNIT_MAP.get(variable, "vector")
                dest = clean_dir / f"chennai_{variable}_{unit}_static.gpkg"
                gdf.to_file(dest, driver="GPKG")
                sidecar = {
                    "layer_id": layer_id,
                    "variable": variable,
                    "unit": unit,
                    "feature_count": int(len(gdf)),
                    "crs": f"EPSG:{target_epsg}",
                    "gap_fill": {"method": "none"},
                    "validation_report": str(report_path),
                    "validation_entry_layer_id": layer_id,
                    "clean_path": str(dest),
                    "cleaned_at": utc_now_iso(),
                }
                write_json(clean_dir / f"{dest.stem}_meta.json", sidecar)
                outputs.append(sidecar)
            continue

        categorical = bool(layer.get("categorical") or variable == "landcover")
        # Prefer finer grid for layers already at 10 m — but shared stack grid is 30 m
        # Spec: all rasters on SAME fixed grid. Landcover native 10 m is warped to shared 30 m
        # OR we keep a parallel 10 m product. Spec requires one shared grid for stacking.
        # WorldCover kept at native in extractor; cleaner warps to shared reference grid.
        native_res = layer.get("native_resolution_m")
        use_grid = grid
        if native_res is not None and float(native_res) < grid.cell_size_m:
            # Finer preferred: build aligned finer grid with same origin snapped
            from chennai_uhi.grid import build_reference_grid

            fine = float(native_res)
            # Same origin family: snap AOI bounds to fine cell, origin compatible with 30 m parent
            bounds = grid.bounds
            use_grid = build_reference_grid(bounds, cell_size_m=fine, epsg=target_epsg)
            # Force shared origin alignment to parent grid
            use_grid = ReferenceGrid(
                epsg=grid.epsg,
                cell_size_m=fine,
                origin_x=grid.origin_x,
                origin_y=grid.origin_y,
                width=int(round(grid.width * grid.cell_size_m / fine)),
                height=int(round(grid.height * grid.cell_size_m / fine)),
                nodata=grid.nodata,
            )
            layer_mask = aoi_mask_for_grid(aoi, use_grid)
        else:
            layer_mask = mask

        nodata = use_grid.nodata
        arr, warp_meta = warp_to_grid(
            src,
            use_grid,
            resampling="nearest" if categorical else "bilinear",
            categorical=categorical,
            nodata=nodata,
        )
        arr = apply_aoi_mask(arr, layer_mask, nodata, categorical=categorical)

        if categorical:
            gap_meta = {
                "method": "none",
                "note": "Categorical land cover — gaps left as class 0 / NoData, not interpolated",
                "remaining_nodata": int(((arr == 0) & layer_mask).sum()),
            }
            out_arr = arr
        else:
            # Only fill where inside AOI
            work = np.where(layer_mask, arr, nodata)
            filled, gap_meta = fill_small_gaps(work, nodata, max_radius_px=max_radius)
            out_arr = apply_aoi_mask(filled, layer_mask, nodata)

        unit = UNIT_MAP.get(variable, layer.get("unit", "na"))
        month = layer.get("month")
        res_m = use_grid.cell_size_m
        fname = _clean_name(variable, unit, month, res_m)
        dest = clean_dir / fname
        dtype = "uint8" if categorical else "float32"
        profile = use_grid.profile(dtype=dtype, nodata=0 if categorical else nodata)
        write_geotiff(dest, out_arr.astype(dtype), profile)

        sidecar = {
            "layer_id": layer_id,
            "variable": variable,
            "unit": unit,
            "file_name": fname,
            "clean_path": str(dest),
            "source_path": src,
            "original_acquisition_dates": None,
            "month": month,
            "resolution_m": res_m,
            "crs": f"EPSG:{use_grid.epsg}",
            "width": use_grid.width,
            "height": use_grid.height,
            "transform": list(use_grid.transform)[:6],
            "grid_origin": [use_grid.origin_x, use_grid.origin_y],
            "gap_fill": gap_meta,
            "warp": warp_meta,
            "sha256": sha256_file(dest),
            "validation_report": str(report_path),
            "validation_entry_layer_id": layer_id,
            "cleaned_at": utc_now_iso(),
        }
        # Pull acquisition dates from validation report if present
        for entry in val_report.get("layers", []):
            if entry.get("layer_id") == layer_id:
                sidecar["original_acquisition_dates"] = entry.get("fetch_record_ref", {}).get(
                    "data_dates"
                )
                sidecar["last_available_date"] = entry.get("fetch_record_ref", {}).get(
                    "last_available_date"
                )
                sidecar["source_name"] = entry.get("fetch_record_ref", {}).get("source_name")
                break
        record = fetch_records.get(layer_id, {})
        if record.get("extra", {}).get("population_source_type"):
            sidecar["population_source_type"] = record["extra"]["population_source_type"]
            sidecar["fallback"] = record["extra"]["population_source_type"] == "synthetic_fallback"

        write_json(clean_dir / f"{Path(fname).stem}_meta.json", sidecar)
        outputs.append(sidecar)
        transforms.append(str(list(use_grid.transform)[:6]))
        shapes.append((use_grid.height, use_grid.width))

    # Coverage / consistency summary
    excluded = []
    for entry in val_report.get("layers", []):
        if entry.get("overall") == "REJECTED":
            cov = None
            for c in entry.get("checks", []):
                if c.get("check") == "spatial_coverage":
                    cov = (c.get("evidence") or {}).get("coverage_fraction")
            excluded.append(
                {
                    "layer_id": entry.get("layer_id"),
                    "variable": entry.get("variable"),
                    "reasons": entry.get("failure_reasons"),
                    "coverage_fraction": cov,
                }
            )
    # Always surface design exclusions
    for var, reason in (val_report.get("design_exclusions") or {}).items():
        if not any(e.get("variable") == var for e in excluded):
            excluded.append({"layer_id": f"{var}_EXCLUDED", "variable": var, "reasons": [reason]})

    # Grid consistency among 30 m stack members
    grid_30 = [o for o in outputs if o.get("resolution_m") == grid.cell_size_m and o.get("width")]
    same_grid = (
        len({(o.get("width"), o.get("height"), tuple(o.get("grid_origin") or [])) for o in grid_30}) <= 1
        if grid_30
        else True
    )

    summary = {
        "stage": "cleaner",
        "temporal_start": cfg["temporal_start"],
        "temporal_end": cfg["temporal_end"],
        "crs": f"EPSG:{target_epsg}",
        "reference_grid": grid.to_dict(),
        "n_clean_outputs": len(outputs),
        "all_30m_layers_pixel_aligned": same_grid,
        "clean_directory": str(clean_dir),
        "excluded_from_clean": excluded,
        "outputs": outputs,
        "note": (
            "Only validator-PASSED layers appear here. "
            "Building height / H-W ratio never enter clean output."
        ),
    }
    write_json(paths["logs"] / "cleaner_summary.json", summary)
    write_json(clean_dir / "CLEAN_DATASET_SUMMARY.json", summary)
    logger.info("CLEANER done — %d outputs in %s", len(outputs), clean_dir)
    return summary
