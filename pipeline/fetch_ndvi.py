import os
import sys
import requests
import rasterio
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "ndvi")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "ndvi")
output_ndvi_composite_file_path = os.path.join(output_folder_for_raw_data, "ndvi_150m_composite.tif")


def fetch_sentinel2_scenes_for_summer_seasons():
    print("Fetching Sentinel-2 Level-2A scenes over the OMR-Pallikaranai study area for summer seasons " + str(study_area.originally_requested_earliest_year) + "-" + str(study_area.originally_requested_latest_year) + " ...")
    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    season_date_ranges = study_area.get_summer_season_date_ranges_for_originally_requested_window()
    season_filters = []
    for year, start_date_text, end_date_text in season_date_ranges:
        season_filters.append(ee.Filter.date(start_date_text, end_date_text))
    combined_season_filter = ee.Filter.Or(*season_filters)

    sentinel2_collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    sentinel2_collection = sentinel2_collection.filterBounds(rectangle)
    sentinel2_collection = sentinel2_collection.filter(combined_season_filter)

    total_scene_count = sentinel2_collection.size().getInfo()
    print("Sentinel-2 scenes returned by Google Earth Engine for the study area and summer seasons: " + str(total_scene_count))

    if total_scene_count == 0:
        print("Fetch failed: zero Sentinel-2 scenes were returned. Nothing to validate.")
        return None, rectangle, season_date_ranges

    print("Fetch succeeded: " + str(total_scene_count) + " candidate scenes found before cloud checking.")
    return sentinel2_collection, rectangle, season_date_ranges


def check_cloud_free_percentage_for_each_scene(sentinel2_collection, rectangle):
    print("Checking how much of each scene is blocked by clouds, using the Scene Classification Layer (SCL) band ...")

    def add_cloud_free_fraction_property(image):
        scl_band = image.select("SCL")
        cloud_shadow_class_value = 3
        medium_cloud_probability_class_value = 8
        high_cloud_probability_class_value = 9
        cirrus_class_value = 10
        is_cloud_free_pixel = scl_band.neq(cloud_shadow_class_value)
        is_cloud_free_pixel = is_cloud_free_pixel.And(scl_band.neq(medium_cloud_probability_class_value))
        is_cloud_free_pixel = is_cloud_free_pixel.And(scl_band.neq(high_cloud_probability_class_value))
        is_cloud_free_pixel = is_cloud_free_pixel.And(scl_band.neq(cirrus_class_value))
        cloud_free_fraction_statistics = is_cloud_free_pixel.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=rectangle,
            scale=20,
            maxPixels=1000000000,
        )
        cloud_free_fraction = ee.Number(cloud_free_fraction_statistics.get("SCL"))
        cloud_free_percentage = cloud_free_fraction.multiply(100)
        return image.set("cloud_free_percentage", cloud_free_percentage)

    collection_with_cloud_information = sentinel2_collection.map(add_cloud_free_fraction_property)

    print("Asking Google Earth Engine to compute cloud-free percentage for every scene, this can take a little while ...")
    scene_timestamps = collection_with_cloud_information.aggregate_array("system:time_start").getInfo()
    scene_cloud_free_percentages = collection_with_cloud_information.aggregate_array("cloud_free_percentage").getInfo()

    print("Received cloud-free percentage results for " + str(len(scene_timestamps)) + " scenes.")
    return collection_with_cloud_information, scene_timestamps, scene_cloud_free_percentages


def summarize_qualifying_scenes_per_summer_season(scene_timestamps, scene_cloud_free_percentages, season_date_ranges):
    import datetime

    print("")
    print("Counting how many cloud-free scenes (>= " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% cloud-free) fall in each summer season ...")

    qualifying_scene_count_per_year = {}
    for year, start_date_text, end_date_text in season_date_ranges:
        qualifying_scene_count_per_year[year] = 0

    for timestamp_milliseconds, cloud_free_percentage in zip(scene_timestamps, scene_cloud_free_percentages):
        scene_date = datetime.datetime.utcfromtimestamp(timestamp_milliseconds / 1000.0)
        if cloud_free_percentage is not None and cloud_free_percentage >= thresholds.required_cloud_free_pixel_percentage_per_scene:
            for year, start_date_text, end_date_text in season_date_ranges:
                start_date = datetime.datetime.strptime(start_date_text, "%Y-%m-%d")
                end_date = datetime.datetime.strptime(end_date_text, "%Y-%m-%d")
                if start_date <= scene_date <= end_date:
                    qualifying_scene_count_per_year[year] = qualifying_scene_count_per_year[year] + 1

    number_of_seasons_meeting_minimum = 0
    for year, start_date_text, end_date_text in season_date_ranges:
        count_for_this_year = qualifying_scene_count_per_year[year]
        meets_minimum = count_for_this_year >= thresholds.minimum_cloud_free_scenes_per_summer_season
        print("Summer " + str(year) + ": " + str(count_for_this_year) + " cloud-free scenes (need >= " + str(thresholds.minimum_cloud_free_scenes_per_summer_season) + ") -> " + ("OK" if meets_minimum else "SHORT"))
        if meets_minimum:
            number_of_seasons_meeting_minimum = number_of_seasons_meeting_minimum + 1

    total_number_of_seasons = len(season_date_ranges)
    fraction_of_seasons_meeting_minimum_full_window = number_of_seasons_meeting_minimum / total_number_of_seasons
    print("")
    print("Seasons meeting the minimum cloud-free scene count, full originally-requested window " + str(study_area.originally_requested_earliest_year) + "-" + str(study_area.originally_requested_latest_year) + ": " + str(number_of_seasons_meeting_minimum) + " out of " + str(total_number_of_seasons) + " (" + str(round(fraction_of_seasons_meeting_minimum_full_window * 100, 1)) + "%)")

    solidly_covered_years = list(range(study_area.solidly_covered_earliest_year, study_area.solidly_covered_latest_year + 1))
    number_of_solid_seasons_meeting_minimum = 0
    for year in solidly_covered_years:
        if qualifying_scene_count_per_year[year] >= thresholds.minimum_cloud_free_scenes_per_summer_season:
            number_of_solid_seasons_meeting_minimum = number_of_solid_seasons_meeting_minimum + 1
    fraction_of_seasons_meeting_minimum_solid_window = number_of_solid_seasons_meeting_minimum / len(solidly_covered_years)
    print("Seasons meeting the minimum cloud-free scene count, solidly-covered window " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + ": " + str(number_of_solid_seasons_meeting_minimum) + " out of " + str(len(solidly_covered_years)) + " (" + str(round(fraction_of_seasons_meeting_minimum_solid_window * 100, 1)) + "%)")
    print("Note: " + str(study_area.originally_requested_earliest_year) + "-" + str(study_area.solidly_covered_earliest_year - 1) + " is documented here as a known coverage gap and is excluded from the keep/drop decision and from the composite build, per user decision to narrow the working analysis window to " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + ".")

    return fraction_of_seasons_meeting_minimum_solid_window, qualifying_scene_count_per_year


def build_ndvi_composite_from_qualifying_scenes(collection_with_cloud_information, rectangle):
    print("")
    print("Building a median NDVI composite from scenes that passed the cloud-free check ...")

    solid_window_start_date = str(study_area.solidly_covered_earliest_year) + "-01-01"
    solid_window_end_date = str(study_area.solidly_covered_latest_year) + "-12-31"

    qualifying_collection = collection_with_cloud_information.filter(
        ee.Filter.gte("cloud_free_percentage", thresholds.required_cloud_free_pixel_percentage_per_scene)
    )
    qualifying_collection = qualifying_collection.filterDate(solid_window_start_date, solid_window_end_date)
    print("Composite restricted to the solidly-covered window " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + " only.")

    def compute_ndvi_band(image):
        ndvi_band = image.normalizedDifference(["B8", "B4"]).rename("NDVI")
        return ndvi_band

    ndvi_image_collection = qualifying_collection.map(compute_ndvi_band)
    ndvi_composite_image = ndvi_image_collection.median().clip(rectangle)

    print("NDVI composite built from the qualifying scenes.")
    return ndvi_composite_image


def download_ndvi_composite_to_local_file(ndvi_composite_image, rectangle):
    print("")
    print("Downloading the NDVI composite at " + str(study_area.analysis_grid_cell_size_meters) + "m resolution to match the analysis grid ...")

    os.makedirs(output_folder_for_raw_data, exist_ok=True)

    download_url = ndvi_composite_image.getDownloadURL({
        "scale": study_area.analysis_grid_cell_size_meters,
        "region": rectangle,
        "format": "GEO_TIFF",
        "crs": "EPSG:4326",
    })

    response = requests.get(download_url, timeout=120)
    if response.status_code != 200:
        print("Download failed with HTTP status code " + str(response.status_code) + ". Response text: " + response.text[:300])
        return None

    with open(output_ndvi_composite_file_path, "wb") as output_file:
        output_file.write(response.content)

    file_size_in_kilobytes = os.path.getsize(output_ndvi_composite_file_path) / 1024.0
    print("Saved NDVI composite to " + output_ndvi_composite_file_path + " (" + str(round(file_size_in_kilobytes, 1)) + " KB)")

    return output_ndvi_composite_file_path


def validate_downloaded_raster(file_path):
    print("")
    print("Opening the downloaded file with rasterio to confirm it is a real, readable raster ...")

    if file_path is None or not os.path.exists(file_path):
        print("Validation failed: no file was downloaded.")
        return False, None, None

    with rasterio.open(file_path) as raster_dataset:
        pixel_width_meters, pixel_height_meters = raster_dataset.res
        raster_width_in_pixels = raster_dataset.width
        raster_height_in_pixels = raster_dataset.height
        coordinate_reference_system = raster_dataset.crs
        ndvi_values = raster_dataset.read(1)

    print("Raster size: " + str(raster_width_in_pixels) + " x " + str(raster_height_in_pixels) + " pixels")
    print("Raster resolution as reported by the file itself: " + str(pixel_width_meters) + " x " + str(pixel_height_meters) + " (units of the CRS: " + str(coordinate_reference_system) + ")")

    valid_pixel_count = int((ndvi_values == ndvi_values).sum())
    total_pixel_count = ndvi_values.size
    valid_pixel_percentage = (valid_pixel_count / total_pixel_count) * 100.0
    print("Pixels with a real NDVI value (not empty/nodata): " + str(round(valid_pixel_percentage, 1)) + "% of the raster")

    minimum_ndvi_value = float(ndvi_values.min())
    maximum_ndvi_value = float(ndvi_values.max())
    print("NDVI value range found in the file: " + str(round(minimum_ndvi_value, 3)) + " to " + str(round(maximum_ndvi_value, 3)) + " (valid NDVI is always between -1 and 1)")

    ndvi_values_are_in_valid_range = minimum_ndvi_value >= -1.0001 and maximum_ndvi_value <= 1.0001
    print("NDVI values are within the physically valid -1 to 1 range: " + str(ndvi_values_are_in_valid_range))

    file_is_valid = valid_pixel_percentage > 50.0 and ndvi_values_are_in_valid_range
    return file_is_valid, valid_pixel_percentage, (minimum_ndvi_value, maximum_ndvi_value)


def decide_keep_or_drop_ndvi(fraction_of_seasons_meeting_minimum, file_is_valid):
    print("")
    print("Deciding whether to KEEP or DROP the NDVI dataset, based on the solidly-covered " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + " window ...")

    seasons_check_passed = fraction_of_seasons_meeting_minimum >= thresholds.required_fraction_of_summer_seasons_meeting_scene_count
    print("Result: " + str(round(fraction_of_seasons_meeting_minimum * 100, 1)) + "% of solidly-covered-window seasons had enough cloud-free scenes >= required " + str(round(thresholds.required_fraction_of_summer_seasons_meeting_scene_count * 100, 1)) + "% -> " + ("PASS" if seasons_check_passed else "FAIL"))
    print("Result: downloaded raster passed file-integrity validation -> " + ("PASS" if file_is_valid else "FAIL"))

    final_decision_is_keep = seasons_check_passed and file_is_valid

    if final_decision_is_keep:
        print("FINAL DECISION: KEEP the NDVI dataset, for the " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + " window. " + str(study_area.originally_requested_earliest_year) + "-" + str(study_area.solidly_covered_earliest_year - 1) + " remains a documented gap, not a silently dropped detail.")
    else:
        print("FINAL DECISION: DROP the NDVI dataset.")
        if not seasons_check_passed:
            print("What is missing: not enough summer seasons have sufficient cloud-free Sentinel-2 coverage even within the narrowed " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + " window.")
        if not file_is_valid:
            print("What is missing: the downloaded NDVI raster failed basic file-integrity checks, so it cannot be trusted as input to the Heat Vulnerability Index.")

    return final_decision_is_keep


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: NDVI feeds the vegetation/cooling-capacity component of the Vulnerability_weight term in the Effectiveness Score. Without it, the Vulnerability_weight term would have no direct measure of green cover, and green-cover-based interventions (tree planting, park siting) could not be distinguished from non-green interventions.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Vegetation Index (NDVI)")
    print("=====================================================")

    sentinel2_collection, rectangle, season_date_ranges = fetch_sentinel2_scenes_for_summer_seasons()
    if sentinel2_collection is None:
        decide_keep_or_drop_ndvi(0.0, False)
        return

    collection_with_cloud_information, scene_timestamps, scene_cloud_free_percentages = check_cloud_free_percentage_for_each_scene(sentinel2_collection, rectangle)

    fraction_of_seasons_meeting_minimum, qualifying_scene_count_per_year = summarize_qualifying_scenes_per_summer_season(
        scene_timestamps, scene_cloud_free_percentages, season_date_ranges
    )

    ndvi_composite_image = build_ndvi_composite_from_qualifying_scenes(collection_with_cloud_information, rectangle)

    downloaded_file_path = download_ndvi_composite_to_local_file(ndvi_composite_image, rectangle)

    file_is_valid, valid_pixel_percentage, ndvi_value_range = validate_downloaded_raster(downloaded_file_path)

    final_decision_is_keep = decide_keep_or_drop_ndvi(fraction_of_seasons_meeting_minimum, file_is_valid)

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        validated_file_path = os.path.join(output_folder_for_validated_data, "ndvi_150m_composite.tif")
        with open(downloaded_file_path, "rb") as source_file:
            file_contents = source_file.read()
        with open(validated_file_path, "wb") as destination_file:
            destination_file.write(file_contents)
        print("")
        print("Copied validated NDVI raster to " + validated_file_path)


if __name__ == "__main__":
    main()
