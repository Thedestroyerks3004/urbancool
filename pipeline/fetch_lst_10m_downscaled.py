import os
import sys
import numpy
import rasterio
import rasterio.warp
from rasterio.enums import Resampling
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import tiled_download

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "lst_10m_downscaled")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "lst_10m_downscaled")

native_10m_sentinel2_file_path = os.path.join("D:\\", "Projects", "UC", "data", "validated", "sentinel2_10m", "sentinel2_10m_bands.tif")

landsat_native_thermal_scale_meters = 30

required_regression_r_squared_for_thermal_sharpening = 0.20

dilated_cloud_bit = 1
cirrus_bit = 2
cloud_bit = 3
cloud_shadow_bit = 4


def add_cloud_free_percentage_property(image, rectangle):
    quality_band = image.select("QA_PIXEL")
    is_clear_pixel = quality_band.bitwiseAnd(1 << dilated_cloud_bit).eq(0)
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cirrus_bit).eq(0))
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cloud_bit).eq(0))
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cloud_shadow_bit).eq(0))
    cloud_free_fraction_statistics = is_clear_pixel.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=rectangle, scale=30, maxPixels=1000000000,
    )
    cloud_free_fraction = ee.Number(cloud_free_fraction_statistics.get("QA_PIXEL"))
    return image.set("cloud_free_percentage", cloud_free_fraction.multiply(100))


def convert_thermal_band_to_celsius(image):
    surface_temperature_kelvin = image.select("ST_B10").multiply(0.00341802).add(149.0)
    surface_temperature_celsius = surface_temperature_kelvin.subtract(273.15).rename("LST_CELSIUS")
    return image.addBands(surface_temperature_celsius)


def fetch_landsat_lst_composite_at_native_scale():
    print("Fetching Landsat 8/9 Collection 2 Level-2 thermal data (the backup LST source, since ECOSTRESS is fetch-blocked without NASA Earthdata credentials) ...")
    print("Honesty note: Landsat's thermal sensor (TIRS) senses at 100m natively; USGS distributes the Surface Temperature product at 30m via co-registration with the reflective bands. 30m is used here as the 'coarse' input to thermal sharpening, not claimed as a native 30m thermal measurement.")

    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    season_date_ranges = study_area.get_summer_season_date_ranges_for_solidly_covered_window()
    season_filters = [ee.Filter.date(start, end) for year, start, end in season_date_ranges]
    combined_season_filter = ee.Filter.Or(*season_filters)

    landsat_collection = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2").merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
    landsat_collection = landsat_collection.filterBounds(rectangle).filter(combined_season_filter)

    total_scene_count = landsat_collection.size().getInfo()
    print("Candidate Landsat scenes found: " + str(total_scene_count))
    if total_scene_count == 0:
        return None, rectangle

    collection_with_cloud_information = landsat_collection.map(lambda image: add_cloud_free_percentage_property(image, rectangle))
    qualifying_collection = collection_with_cloud_information.filter(
        ee.Filter.gte("cloud_free_percentage", thresholds.required_cloud_free_pixel_percentage_per_scene)
    )
    qualifying_scene_count = qualifying_collection.size().getInfo()
    print("Scenes passing the " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% cloud-free threshold: " + str(qualifying_scene_count))
    if qualifying_scene_count == 0:
        return None, rectangle

    lst_composite_image = qualifying_collection.map(convert_thermal_band_to_celsius).select("LST_CELSIUS").median().clip(rectangle)
    print("Built median LST composite (Celsius) from " + str(qualifying_scene_count) + " qualifying scenes.")
    return lst_composite_image, rectangle


def download_landsat_lst_at_30m(lst_composite_image):
    print("")
    downloaded_file_path = tiled_download.download_ee_image_in_tiles(
        lst_composite_image,
        study_area.study_area_minimum_longitude,
        study_area.study_area_minimum_latitude,
        study_area.study_area_maximum_longitude,
        study_area.study_area_maximum_latitude,
        scale_in_meters=landsat_native_thermal_scale_meters,
        tiles_per_side=3,
        output_folder=output_folder_for_raw_data,
        base_filename="landsat_lst_30m",
    )
    return downloaded_file_path


def load_native_10m_ndvi():
    print("")
    print("Loading the already-fetched, already-validated native-10m Sentinel-2 bands to compute NDVI as the sharpening covariate ...")

    if not os.path.exists(native_10m_sentinel2_file_path):
        print("Cannot proceed: native-10m Sentinel-2 file not found at " + native_10m_sentinel2_file_path + ". Run fetch_sentinel2_10m_native.py first.")
        return None, None, None

    with rasterio.open(native_10m_sentinel2_file_path) as raster_dataset:
        band_values = raster_dataset.read()
        ndvi_transform = raster_dataset.transform
        ndvi_crs = raster_dataset.crs

    red_band = band_values[2]
    nir_band = band_values[3]
    with numpy.errstate(invalid="ignore", divide="ignore"):
        ndvi_10m = (nir_band - red_band) / (nir_band + red_band)

    print("Computed native-10m NDVI array, shape " + str(ndvi_10m.shape))
    return ndvi_10m, ndvi_transform, ndvi_crs


def resample_array_to_match_target_grid(source_array, source_transform, source_crs, target_shape, target_transform, target_crs, resampling_method):
    destination_array = numpy.empty(target_shape, dtype=numpy.float32)
    rasterio.warp.reproject(
        source=source_array,
        destination=destination_array,
        src_transform=source_transform,
        src_crs=source_crs,
        dst_transform=target_transform,
        dst_crs=target_crs,
        resampling=resampling_method,
    )
    return destination_array


def fit_thermal_sharpening_regression(lst_30m, ndvi_30m):
    print("")
    print("Fitting the TsHARP-style regression: LST (30m) as a function of NDVI (aggregated to 30m) ...")

    valid_mask = (~numpy.isnan(lst_30m)) & (~numpy.isnan(ndvi_30m))
    valid_lst_values = lst_30m[valid_mask]
    valid_ndvi_values = ndvi_30m[valid_mask]

    print("Valid paired pixels available for regression: " + str(len(valid_lst_values)))

    regression_coefficients = numpy.polyfit(valid_ndvi_values, valid_lst_values, deg=1)
    slope, intercept = regression_coefficients[0], regression_coefficients[1]

    predicted_lst_at_valid_pixels = slope * valid_ndvi_values + intercept
    residuals = valid_lst_values - predicted_lst_at_valid_pixels
    sum_of_squares_residual = numpy.sum(residuals ** 2)
    sum_of_squares_total = numpy.sum((valid_lst_values - numpy.mean(valid_lst_values)) ** 2)
    r_squared = 1.0 - (sum_of_squares_residual / sum_of_squares_total)

    print("Regression: LST_celsius = " + str(round(slope, 3)) + " * NDVI + " + str(round(intercept, 3)))
    print("R-squared of the coarse-scale LST-NDVI regression: " + str(round(r_squared, 3)))

    return slope, intercept, r_squared


def decide_keep_or_drop_on_regression_strength(r_squared):
    print("")
    print("Deciding whether the LST-NDVI relationship is strong enough to justify thermal sharpening to 10m ...")

    regression_check_passed = r_squared >= required_regression_r_squared_for_thermal_sharpening
    print("Result: R-squared " + str(round(r_squared, 3)) + " >= required " + str(required_regression_r_squared_for_thermal_sharpening) + " -> " + ("PASS" if regression_check_passed else "FAIL"))

    if not regression_check_passed:
        print("What is missing: the vegetation-temperature relationship in this corridor is too weak to justify sharpening LST using NDVI as a covariate. Producing a 10m LST from this regression would manufacture false spatial detail not actually supported by the data.")

    return regression_check_passed


def apply_sharpening_and_correct_residuals(slope, intercept, ndvi_10m, ndvi_10m_transform, ndvi_10m_crs, lst_30m, lst_30m_transform, lst_30m_crs):
    print("")
    print("Applying the regression to native-10m NDVI to predict a first-pass 10m LST surface ...")

    predicted_lst_10m = slope * ndvi_10m + intercept

    print("Aggregating the predicted 10m LST back to the original 30m grid to compute the residual correction (standard TsHARP residual step) ...")
    predicted_lst_aggregated_to_30m = resample_array_to_match_target_grid(
        predicted_lst_10m, ndvi_10m_transform, ndvi_10m_crs,
        lst_30m.shape, lst_30m_transform, lst_30m_crs,
        Resampling.average,
    )

    residual_30m = lst_30m - predicted_lst_aggregated_to_30m

    print("Resampling the residual back down to 10m and adding it as a correction ...")
    residual_10m = resample_array_to_match_target_grid(
        residual_30m, lst_30m_transform, lst_30m_crs,
        ndvi_10m.shape, ndvi_10m_transform, ndvi_10m_crs,
        Resampling.bilinear,
    )

    sharpened_lst_10m = predicted_lst_10m + residual_10m
    print("Produced final residual-corrected sharpened LST at 10m, shape " + str(sharpened_lst_10m.shape))

    return sharpened_lst_10m


def validate_self_consistency(sharpened_lst_10m, ndvi_10m_transform, ndvi_10m_crs, lst_30m, lst_30m_transform, lst_30m_crs):
    print("")
    print("Running the self-consistency check: reaggregating the sharpened 10m LST back to 30m and comparing to the real 30m Landsat measurement ...")
    print("Honesty note: this checks whether the sharpening preserved the original coarse signal (a necessary condition), NOT whether the 10m spatial detail it adds is independently correct -- there is no true 10m thermal ground truth available to test that.")

    reaggregated_lst_30m = resample_array_to_match_target_grid(
        sharpened_lst_10m, ndvi_10m_transform, ndvi_10m_crs,
        lst_30m.shape, lst_30m_transform, lst_30m_crs,
        Resampling.average,
    )

    valid_mask = (~numpy.isnan(reaggregated_lst_30m)) & (~numpy.isnan(lst_30m))
    root_mean_squared_error = numpy.sqrt(numpy.mean((reaggregated_lst_30m[valid_mask] - lst_30m[valid_mask]) ** 2))
    print("Self-consistency RMSE (reaggregated 10m-sharpened vs real 30m Landsat LST): " + str(round(root_mean_squared_error, 4)) + " degrees Celsius")

    return root_mean_squared_error


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: this is the only candidate 10m-consistent LST layer available without NASA Earthdata credentials. Without it, the Effectiveness Score's Delta LST numerator would have to fall back to a coarser grid, breaking the 10m-only requirement for this track.")
    else:
        print("Necessity check skipped because the dataset was dropped. Without a valid 10m LST, this 10m-only track cannot compute Delta LST at all -- ECOSTRESS access (with real NASA Earthdata credentials) is the only remaining path to a 10m-class thermal layer.")


def main():
    print("=====================================================")
    print("FETCHING, DOWNSCALING, AND VALIDATING: LST at 10m (Landsat backup + TsHARP-style sharpening)")
    print("=====================================================")

    lst_composite_image, rectangle = fetch_landsat_lst_composite_at_native_scale()
    if lst_composite_image is None:
        print("FINAL DECISION: DROP. No usable Landsat LST composite could be built.")
        return

    landsat_lst_30m_path = download_landsat_lst_at_30m(lst_composite_image)
    if landsat_lst_30m_path is None:
        print("FINAL DECISION: DROP. Landsat LST download failed.")
        return

    ndvi_10m, ndvi_10m_transform, ndvi_10m_crs = load_native_10m_ndvi()
    if ndvi_10m is None:
        print("FINAL DECISION: DROP. Native-10m NDVI covariate is not available.")
        return

    with rasterio.open(landsat_lst_30m_path) as raster_dataset:
        lst_30m = raster_dataset.read(1)
        lst_30m_transform = raster_dataset.transform
        lst_30m_crs = raster_dataset.crs

    print("")
    print("Aligning native-10m NDVI onto the Landsat 30m grid by area-averaging, so the two can be compared pixel-for-pixel ...")
    ndvi_30m = resample_array_to_match_target_grid(
        ndvi_10m, ndvi_10m_transform, ndvi_10m_crs,
        lst_30m.shape, lst_30m_transform, lst_30m_crs,
        Resampling.average,
    )

    slope, intercept, r_squared = fit_thermal_sharpening_regression(lst_30m, ndvi_30m)

    regression_check_passed = decide_keep_or_drop_on_regression_strength(r_squared)
    if not regression_check_passed:
        print("")
        print("FINAL DECISION: DROP the 10m LST dataset.")
        explain_necessity_for_effectiveness_score(False)
        return

    sharpened_lst_10m = apply_sharpening_and_correct_residuals(
        slope, intercept, ndvi_10m, ndvi_10m_transform, ndvi_10m_crs,
        lst_30m, lst_30m_transform, lst_30m_crs,
    )

    self_consistency_rmse = validate_self_consistency(
        sharpened_lst_10m, ndvi_10m_transform, ndvi_10m_crs,
        lst_30m, lst_30m_transform, lst_30m_crs,
    )

    plausible_minimum_celsius = 15.0
    plausible_maximum_celsius = 60.0
    valid_values = sharpened_lst_10m[~numpy.isnan(sharpened_lst_10m)]
    values_are_plausible = float(valid_values.min()) >= plausible_minimum_celsius and float(valid_values.max()) <= plausible_maximum_celsius
    print("")
    print("Sharpened 10m LST value range: " + str(round(float(valid_values.min()), 1)) + "C to " + str(round(float(valid_values.max()), 1)) + "C")
    print("Result: values within plausible " + str(plausible_minimum_celsius) + "-" + str(plausible_maximum_celsius) + "C range -> " + ("PASS" if values_are_plausible else "FAIL"))

    final_decision_is_keep = regression_check_passed and values_are_plausible

    print("")
    print("FINAL DECISION: " + ("KEEP" if final_decision_is_keep else "DROP") + " the 10m LST dataset (regression R-squared " + str(round(r_squared, 3)) + ", self-consistency RMSE " + str(round(self_consistency_rmse, 4)) + "C).")
    print("Labeling requirement for downstream use: this layer MUST be documented as 'thermally sharpened to 10m via NDVI-based regression, not a direct 10m thermal measurement' wherever it is used.")

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        validated_file_path = os.path.join(output_folder_for_validated_data, "lst_10m_sharpened_celsius.tif")
        output_profile = {
            "driver": "GTiff",
            "height": sharpened_lst_10m.shape[0],
            "width": sharpened_lst_10m.shape[1],
            "count": 1,
            "dtype": "float32",
            "crs": ndvi_10m_crs,
            "transform": ndvi_10m_transform,
            "nodata": float("nan"),
        }
        with rasterio.open(validated_file_path, "w", **output_profile) as output_raster:
            output_raster.write(sharpened_lst_10m.astype(numpy.float32), 1)
        print("Saved validated sharpened 10m LST raster to " + validated_file_path)


if __name__ == "__main__":
    main()
