"""
Builds the native-10m per-pixel feature stack for the full AOI, in a single streaming
pass over the 45 already-validated months (no per-pixel data is loaded for all 45 months
at once -- this processes one month at a time and accumulates running statistics, to keep
memory bounded regardless of how many months exist).

Log lines are printed at each real step (not silent) per this project's established
convention: every fetch/compute step announces what it is doing and reports real numbers,
not just a final "done".
"""

import os
import glob
import time
import numpy as np
import rasterio
import rasterio.features
import geopandas as gpd
from scipy.ndimage import uniform_filter

from app.albedo import compute_albedo_from_raw_bands, ALBEDO_METHOD_METADATA

# Where the offline pipeline (unnecessary/pipeline/) wrote its output. If you change
# these, the pipeline scripts that write to them must be changed to match, or this
# module will find nothing and every feature will come back as NaN.
DATA_DIR = "D:\\Projects\\UC\\data\\validated"
CACHE_DIR = "D:\\Projects\\UC\\backend\\cache"

SENTINEL2_BANDS_DIR = os.path.join(DATA_DIR, "sentinel2_10m_monthly_least_cloudy")
NDVI_DIR = os.path.join(DATA_DIR, "ndvi_10m_monthly")
NDWI_DIR = os.path.join(DATA_DIR, "water_ndwi_10m_monthly")
LULC_DIR = os.path.join(DATA_DIR, "lulc_10m_monthly")
BUILDINGS_PATH = os.path.join(DATA_DIR, "building_footprints", "building_footprints.geojson")
ROADS_PATH = os.path.join(DATA_DIR, "road_network", "road_network.geojson")

# The land-cover classifier (unnecessary/pipeline/derive/train_and_apply_monthly_lulc.py)
# writes these exact integer codes into every LULC raster. Changing a number here without
# changing it there (or vice versa) silently relabels pixels -- e.g. if the classifier's
# "built-up" class becomes code 3 but this stays 0, built_up_pct_mean would compute from
# whatever code 0 now means, wrong and with no error raised.
LULC_BUILT_UP_CODE = 0
LULC_VEGETATION_CODE = 1
LULC_WATER_CODE = 2
LULC_NODATA_CODE = 255

# Side length, in pixels, of the moving window used to turn point/line vector data (OSM
# buildings/roads) into a per-pixel density. 9 pixels = 90m x 90m. Raising this smooths
# density over a wider neighborhood (less noisy, but blurs out small dense clusters);
# lowering it makes density more locally sensitive but noisier. Must stay odd so the
# window has a true center pixel.
BUILDING_DENSITY_WINDOW_PIXELS = 9
ROAD_DENSITY_WINDOW_PIXELS = 9
# The native resolution of every raster this project uses. Changing this without actually
# re-fetching data at a different resolution would silently mis-scale every density and
# distance calculation below -- it must match the real pixel size of the source rasters,
# not be treated as a tunable setting.
PIXEL_SIZE_METERS = 10.0

FEATURE_STACK_CACHE_PATH = os.path.join(CACHE_DIR, "feature_stack_10m.npz")
# The 14 columns the trained model expects, in this exact order (CatBoost stores feature
# order, not names, internally). Adding/removing/reordering a name here means the model
# in backend/models/ no longer matches this code and must be retrained (backend/app/
# model.py) before the API can serve predictions again -- it will not error loudly, it
# will just silently feed the wrong column of data into the wrong learned split.
FEATURE_NAMES = [
    "ndvi_mean", "ndvi_min", "ndvi_std",
    "ndwi_mean", "ndwi_std",
    "albedo_mean", "albedo_min", "albedo_std",
    "built_up_pct_mean", "vegetation_pct_mean", "water_pct_mean",
    "built_up_pct_trend",
    "building_density_per_km2",
    "road_density_km_per_km2",
]


def log(msg):
    print(f"[feature_stack] {msg}", flush=True)


def list_available_months():
    """Only months where NDVI, NDWI, LULC, and the raw bands all exist are usable -- this
    finds that intersection instead of assuming every month is complete."""
    ndvi_files = sorted(glob.glob(os.path.join(NDVI_DIR, "*.tif")))
    months = []
    for f in ndvi_files:
        year_month = os.path.basename(f).replace("ndvi_10m_", "").replace(".tif", "")
        band_file = os.path.join(SENTINEL2_BANDS_DIR, year_month, f"sentinel2_10m_least_cloudy_{year_month}.tif")
        ndwi_file = os.path.join(NDWI_DIR, f"ndwi_10m_{year_month}.tif")
        lulc_file = os.path.join(LULC_DIR, year_month, f"lulc_10m_{year_month}.tif")
        if os.path.exists(band_file) and os.path.exists(ndwi_file) and os.path.exists(lulc_file):
            months.append({"year_month": year_month, "bands": band_file, "ndvi": f, "ndwi": ndwi_file, "lulc": lulc_file})
    return sorted(months, key=lambda m: m["year_month"])


def get_reference_grid():
    """Every month's raster shares the same grid, so any one NDVI file's transform/shape/
    CRS defines the pixel grid every feature is computed on."""
    reference_file = sorted(glob.glob(os.path.join(NDVI_DIR, "*.tif")))[0]
    with rasterio.open(reference_file) as ds:
        return ds.transform, (ds.height, ds.width), ds.crs, ds.bounds


def rasterize_building_density(transform, shape, crs):
    log(f"Rasterizing building footprints to the native 10m grid, then applying a {BUILDING_DENSITY_WINDOW_PIXELS}x{BUILDING_DENSITY_WINDOW_PIXELS}-pixel moving-window density estimate ...")
    buildings = gpd.read_file(BUILDINGS_PATH)
    buildings = buildings[buildings.geometry.is_valid]
    buildings_wgs84 = buildings.to_crs(crs)
    centroids = buildings_wgs84.geometry.centroid

    point_raster = rasterio.features.rasterize(
        [(geom, 1) for geom in centroids],
        out_shape=shape, transform=transform, fill=0, dtype="float32", merge_alg=rasterio.enums.MergeAlg.add,
    )
    log(f"Rasterized {int(point_raster.sum())} building centroids onto the 10m grid (of {len(buildings_wgs84)} total buildings).")

    window_building_count = uniform_filter(point_raster, size=BUILDING_DENSITY_WINDOW_PIXELS, mode="constant") * (BUILDING_DENSITY_WINDOW_PIXELS ** 2)
    window_area_km2 = ((BUILDING_DENSITY_WINDOW_PIXELS * PIXEL_SIZE_METERS) ** 2) / 1e6
    building_density_per_km2 = window_building_count / window_area_km2

    log(f"Building density per km2: min={building_density_per_km2.min():.1f}, mean={building_density_per_km2.mean():.1f}, max={building_density_per_km2.max():.1f}")
    return building_density_per_km2.astype(np.float32)


def rasterize_road_density(transform, shape, crs):
    log(f"Rasterizing roads to the native 10m grid (presence mask, all_touched), then applying a {ROAD_DENSITY_WINDOW_PIXELS}x{ROAD_DENSITY_WINDOW_PIXELS}-pixel moving-window density approximation ...")
    log("Honesty note: this is a presence-based approximation (fraction of window pixels a road touches, scaled by pixel size), not an exact vector-length-per-km2 calculation like the zonal model used.")
    roads = gpd.read_file(ROADS_PATH)
    roads = roads[roads.geometry.is_valid]
    roads_wgs84 = roads.to_crs(crs)

    presence_raster = rasterio.features.rasterize(
        [(geom, 1) for geom in roads_wgs84.geometry],
        out_shape=shape, transform=transform, fill=0, dtype="uint8", all_touched=True,
    )
    log(f"Road-presence pixels: {int(presence_raster.sum())} of {presence_raster.size} total.")

    window_presence_fraction = uniform_filter(presence_raster.astype(np.float32), size=ROAD_DENSITY_WINDOW_PIXELS, mode="constant")
    window_side_km = (ROAD_DENSITY_WINDOW_PIXELS * PIXEL_SIZE_METERS) / 1000.0
    window_area_km2 = window_side_km ** 2
    # Convert presence fraction back to an actual touched-pixel count, then to a real length,
    # before dividing by window area -- the earlier version skipped the touched-pixel-count step
    # and was off by exactly ROAD_DENSITY_WINDOW_PIXELS**2 / ROAD_DENSITY_WINDOW_PIXELS = a factor of 9.
    touched_pixel_count = window_presence_fraction * (ROAD_DENSITY_WINDOW_PIXELS ** 2)
    length_km_in_window = touched_pixel_count * (PIXEL_SIZE_METERS / 1000.0)
    road_density_km_per_km2 = length_km_in_window / window_area_km2

    log(f"Road density (approx) km/km2: min={road_density_km_per_km2.min():.2f}, mean={road_density_km_per_km2.mean():.2f}, max={road_density_km_per_km2.max():.2f}")
    return road_density_km_per_km2.astype(np.float32)


def build_feature_stack(force_rebuild=False):
    """Compute (or load from cache) every per-pixel feature for the whole AOI.

    Runs one pass over the months, keeping only running totals per pixel (sum, sum of
    squares, min, count) instead of stacking all months in memory -- mean/std/min are
    then a few lines of arithmetic on those totals once the pass finishes. The built-up
    trend uses the same idea: the closed-form least-squares slope only needs five running
    sums (sum of t, sum of y, sum of t*y, sum of t^2, and count), not the full time series.

    force_rebuild=False (the default, and what the live API always uses): if the cache
    file already exists, load it in under a second instead of recomputing. Every request
    to the running server reuses this same cached result -- it is never recomputed per
    request.
    force_rebuild=True: ignore the cache and recompute from data/validated/ from scratch
    (several minutes). Only pass this when the source data actually changed -- e.g. after
    running the fetching/ scripts again -- otherwise you're just paying the cost for the
    same answer."""
    if os.path.exists(FEATURE_STACK_CACHE_PATH) and not force_rebuild:
        log(f"Cached feature stack found at {FEATURE_STACK_CACHE_PATH}, loading instead of recomputing.")
        cached = np.load(FEATURE_STACK_CACHE_PATH, allow_pickle=True)
        return {name: cached[name] for name in FEATURE_NAMES}, dict(cached["meta_shape"])

    log("=====================================================")
    log("BUILDING NATIVE-10m PIXEL FEATURE STACK (full AOI, single streaming pass over months)")
    log("=====================================================")

    transform, shape, crs, bounds = get_reference_grid()
    height, width = shape
    total_pixels = height * width
    log(f"Grid: {width} x {height} = {total_pixels} pixels at 10m native resolution, CRS {crs}")

    months = list_available_months()
    log(f"Months with a complete set of bands+NDVI+NDWI+LULC: {len(months)}")

    ndvi_sum = np.zeros(shape, dtype=np.float64)
    ndvi_sumsq = np.zeros(shape, dtype=np.float64)
    ndvi_min = np.full(shape, np.inf, dtype=np.float64)
    ndvi_count = np.zeros(shape, dtype=np.int32)

    ndwi_sum = np.zeros(shape, dtype=np.float64)
    ndwi_sumsq = np.zeros(shape, dtype=np.float64)
    ndwi_count = np.zeros(shape, dtype=np.int32)

    albedo_sum = np.zeros(shape, dtype=np.float64)
    albedo_sumsq = np.zeros(shape, dtype=np.float64)
    albedo_min = np.full(shape, np.inf, dtype=np.float64)
    albedo_count = np.zeros(shape, dtype=np.int32)

    built_up_count = np.zeros(shape, dtype=np.int32)
    vegetation_count = np.zeros(shape, dtype=np.int32)
    water_count = np.zeros(shape, dtype=np.int32)
    lulc_valid_count = np.zeros(shape, dtype=np.int32)

    trend_sum_t = np.zeros(shape, dtype=np.float64)
    trend_sum_y = np.zeros(shape, dtype=np.float64)
    trend_sum_ty = np.zeros(shape, dtype=np.float64)
    trend_sum_tt = np.zeros(shape, dtype=np.float64)
    trend_n = np.zeros(shape, dtype=np.int32)

    total_clipped_below = 0
    total_clipped_above = 0

    start_time = time.time()
    for month_index, month in enumerate(months):
        with rasterio.open(month["ndvi"]) as ds:
            ndvi_arr = ds.read(1).astype(np.float64)
        with rasterio.open(month["ndwi"]) as ds:
            ndwi_arr = ds.read(1).astype(np.float64)
        with rasterio.open(month["lulc"]) as ds:
            lulc_arr = ds.read(1)
        with rasterio.open(month["bands"]) as ds:
            raw_bands = ds.read()

        ndvi_valid = ~np.isnan(ndvi_arr)
        ndvi_sum[ndvi_valid] += ndvi_arr[ndvi_valid]
        ndvi_sumsq[ndvi_valid] += ndvi_arr[ndvi_valid] ** 2
        ndvi_min[ndvi_valid] = np.minimum(ndvi_min[ndvi_valid], ndvi_arr[ndvi_valid])
        ndvi_count[ndvi_valid] += 1

        ndwi_valid = ~np.isnan(ndwi_arr)
        ndwi_sum[ndwi_valid] += ndwi_arr[ndwi_valid]
        ndwi_sumsq[ndwi_valid] += ndwi_arr[ndwi_valid] ** 2
        ndwi_count[ndwi_valid] += 1

        albedo_arr, validation_report = compute_albedo_from_raw_bands(raw_bands[0], raw_bands[2], raw_bands[3])
        albedo_valid = np.any(raw_bands != 0, axis=0)
        total_clipped_below += validation_report["pixels_clipped_below_zero"]
        total_clipped_above += validation_report["pixels_clipped_above_one"]
        albedo_sum[albedo_valid] += albedo_arr[albedo_valid]
        albedo_sumsq[albedo_valid] += albedo_arr[albedo_valid] ** 2
        albedo_min[albedo_valid] = np.minimum(albedo_min[albedo_valid], albedo_arr[albedo_valid])
        albedo_count[albedo_valid] += 1

        lulc_valid = lulc_arr != LULC_NODATA_CODE
        lulc_valid_count[lulc_valid] += 1
        is_built_up = lulc_arr == LULC_BUILT_UP_CODE
        built_up_count[lulc_valid & is_built_up] += 1
        vegetation_count[lulc_valid & (lulc_arr == LULC_VEGETATION_CODE)] += 1
        water_count[lulc_valid & (lulc_arr == LULC_WATER_CODE)] += 1

        y = np.where(lulc_valid, is_built_up.astype(np.float64), 0.0)
        t = float(month_index)
        trend_sum_t[lulc_valid] += t
        trend_sum_y[lulc_valid] += y[lulc_valid]
        trend_sum_ty[lulc_valid] += t * y[lulc_valid]
        trend_sum_tt[lulc_valid] += t * t
        trend_n[lulc_valid] += 1

        elapsed = time.time() - start_time
        log(f"Processed month {month_index + 1} of {len(months)} ({month['year_month']}) -- {elapsed:.1f}s elapsed")

    log(f"Albedo clipping across all months: {total_clipped_below} pixel-months below 0, {total_clipped_above} pixel-months above 1 (of {total_pixels * len(months)} pixel-months total)")

    log("Finalizing mean/min/std for NDVI, NDWI, Albedo ...")
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi_mean = np.where(ndvi_count > 0, ndvi_sum / np.maximum(ndvi_count, 1), np.nan)
        ndvi_variance = np.where(ndvi_count > 0, ndvi_sumsq / np.maximum(ndvi_count, 1) - ndvi_mean ** 2, np.nan)
        ndvi_std = np.sqrt(np.maximum(ndvi_variance, 0))
        ndvi_min_final = np.where(ndvi_count > 0, ndvi_min, np.nan)

        ndwi_mean = np.where(ndwi_count > 0, ndwi_sum / np.maximum(ndwi_count, 1), np.nan)
        ndwi_variance = np.where(ndwi_count > 0, ndwi_sumsq / np.maximum(ndwi_count, 1) - ndwi_mean ** 2, np.nan)
        ndwi_std = np.sqrt(np.maximum(ndwi_variance, 0))

        albedo_mean = np.where(albedo_count > 0, albedo_sum / np.maximum(albedo_count, 1), np.nan)
        albedo_variance = np.where(albedo_count > 0, albedo_sumsq / np.maximum(albedo_count, 1) - albedo_mean ** 2, np.nan)
        albedo_std = np.sqrt(np.maximum(albedo_variance, 0))
        albedo_min_final = np.where(albedo_count > 0, albedo_min, np.nan)

        built_up_pct_mean = np.where(lulc_valid_count > 0, built_up_count / np.maximum(lulc_valid_count, 1) * 100.0, np.nan)
        vegetation_pct_mean = np.where(lulc_valid_count > 0, vegetation_count / np.maximum(lulc_valid_count, 1) * 100.0, np.nan)
        water_pct_mean = np.where(lulc_valid_count > 0, water_count / np.maximum(lulc_valid_count, 1) * 100.0, np.nan)

        trend_denominator = trend_n * trend_sum_tt - trend_sum_t ** 2
        built_up_pct_trend = np.where(
            (trend_n >= 3) & (np.abs(trend_denominator) > 1e-9),
            (trend_n * trend_sum_ty - trend_sum_t * trend_sum_y) / np.where(trend_denominator == 0, np.nan, trend_denominator),
            np.nan,
        )

    log(f"NDVI mean range: {np.nanmin(ndvi_mean):.3f} to {np.nanmax(ndvi_mean):.3f}")
    log(f"NDWI mean range: {np.nanmin(ndwi_mean):.3f} to {np.nanmax(ndwi_mean):.3f}")
    log(f"Albedo mean range: {np.nanmin(albedo_mean):.3f} to {np.nanmax(albedo_mean):.3f}")
    log(f"Built-up pct mean range: {np.nanmin(built_up_pct_mean):.1f} to {np.nanmax(built_up_pct_mean):.1f}")

    building_density_per_km2 = rasterize_building_density(transform, shape, crs)
    road_density_km_per_km2 = rasterize_road_density(transform, shape, crs)

    feature_arrays = {
        "ndvi_mean": ndvi_mean.astype(np.float32), "ndvi_min": ndvi_min_final.astype(np.float32), "ndvi_std": ndvi_std.astype(np.float32),
        "ndwi_mean": ndwi_mean.astype(np.float32), "ndwi_std": ndwi_std.astype(np.float32),
        "albedo_mean": albedo_mean.astype(np.float32), "albedo_min": albedo_min_final.astype(np.float32), "albedo_std": albedo_std.astype(np.float32),
        "built_up_pct_mean": built_up_pct_mean.astype(np.float32), "vegetation_pct_mean": vegetation_pct_mean.astype(np.float32), "water_pct_mean": water_pct_mean.astype(np.float32),
        "built_up_pct_trend": built_up_pct_trend.astype(np.float32),
        "building_density_per_km2": building_density_per_km2,
        "road_density_km_per_km2": road_density_km_per_km2,
    }

    os.makedirs(CACHE_DIR, exist_ok=True)
    meta = {"height": height, "width": width, "transform": list(transform)[:6], "crs": str(crs), "n_months": len(months)}
    log(f"Caching feature stack to {FEATURE_STACK_CACHE_PATH} ...")
    np.savez_compressed(FEATURE_STACK_CACHE_PATH, meta_shape=np.array(list(meta.items()), dtype=object), **feature_arrays)
    log("Feature stack build complete.")

    return feature_arrays, meta
