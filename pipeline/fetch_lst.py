import os
import sys
import datetime
import requests
import rasterio
import numpy
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "lst")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "lst")
output_lst_composite_file_path = os.path.join(output_folder_for_raw_data, "lst_150m_composite_celsius.tif")

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
        reducer=ee.Reducer.mean(),
        geometry=rectangle,
        scale=30,
        maxPixels=1000000000,
    )
    cloud_free_fraction = ee.Number(cloud_free_fraction_statistics.get("QA_PIXEL"))
    cloud_free_percentage = cloud_free_fraction.multiply(100)
    return image.set("cloud_free_percentage", cloud_free_percentage)


def convert_thermal_band_to_celsius(image):
    surface_temperature_kelvin = image.select("ST_B10").multiply(0.00341802).add(149.0)
    surface_temperature_celsius = surface_temperature_kelvin.subtract(273.15).rename("LST_CELSIUS")
    return image.addBands(surface_temperature_celsius)


def fetch_landsat_scenes_for_summer_seasons():
    print("Fetching Landsat 8/9 Collection 2 Level-2 scenes (Band 10 thermal) over the solidly-covered window " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + " ...")
    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    season_date_ranges = study_area.get_summer_season_date_ranges_for_solidly_covered_window()
    season_filters = [ee.Filter.date(start, end) for year, start, end in season_date_ranges]
    combined_season_filter = ee.Filter.Or(*season_filters)

    landsat8_collection = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
    landsat9_collection = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
    combined_landsat_collection = landsat8_collection.merge(landsat9_collection)

    combined_landsat_collection = combined_landsat_collection.filterBounds(rectangle)
    combined_landsat_collection = combined_landsat_collection.filter(combined_season_filter)

    total_scene_count = combined_landsat_collection.size().getInfo()
    print("Landsat 8+9 scenes returned by Google Earth Engine for the study area and summer seasons: " + str(total_scene_count))

    if total_scene_count == 0:
        print("Fetch failed: zero Landsat scenes were returned.")
        return None, rectangle, season_date_ranges

    print("Fetch succeeded: " + str(total_scene_count) + " candidate scenes found before cloud checking.")
    return combined_landsat_collection, rectangle, season_date_ranges


def check_cloud_free_percentage_for_each_scene(landsat_collection, rectangle):
    print("Checking how much of each scene is blocked by clouds, using the Landsat QA_PIXEL band ...")

    collection_with_cloud_information = landsat_collection.map(lambda image: add_cloud_free_percentage_property(image, rectangle))

    print("Asking Google Earth Engine to compute cloud-free percentage for every scene, this can take a little while ...")
    scene_timestamps = collection_with_cloud_information.aggregate_array("system:time_start").getInfo()
    scene_cloud_free_percentages = collection_with_cloud_information.aggregate_array("cloud_free_percentage").getInfo()

    print("Received cloud-free percentage results for " + str(len(scene_timestamps)) + " scenes.")
    return collection_with_cloud_information, scene_timestamps, scene_cloud_free_percentages


def summarize_qualifying_scenes_per_summer_season(scene_timestamps, scene_cloud_free_percentages, season_date_ranges):
    print("")
    print("Counting how many cloud-free scenes (>= " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% cloud-free) fall in each summer season ...")

    qualifying_scene_count_per_year = {year: 0 for year, start, end in season_date_ranges}

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
    fraction_of_seasons_meeting_minimum = number_of_seasons_meeting_minimum / total_number_of_seasons
    print("")
    print("Seasons meeting the minimum cloud-free scene count: " + str(number_of_seasons_meeting_minimum) + " out of " + str(total_number_of_seasons) + " (" + str(round(fraction_of_seasons_meeting_minimum * 100, 1)) + "%)")

    return fraction_of_seasons_meeting_minimum, qualifying_scene_count_per_year


def build_lst_composite_from_qualifying_scenes(collection_with_cloud_information, rectangle):
    print("")
    print("Building a median Land Surface Temperature composite (in Celsius) from scenes that passed the cloud-free check ...")

    qualifying_collection = collection_with_cloud_information.filter(
        ee.Filter.gte("cloud_free_percentage", thresholds.required_cloud_free_pixel_percentage_per_scene)
    )

    qualifying_collection_with_celsius = qualifying_collection.map(convert_thermal_band_to_celsius)
    lst_composite_image = qualifying_collection_with_celsius.select("LST_CELSIUS").median().clip(rectangle)

    print("LST composite built from the qualifying scenes.")
    return lst_composite_image


def download_lst_composite_to_local_file(lst_composite_image, rectangle):
    print("")
    print("Downloading the LST composite at " + str(study_area.analysis_grid_cell_size_meters) + "m resolution to match the analysis grid ...")

    os.makedirs(output_folder_for_raw_data, exist_ok=True)

    download_url = lst_composite_image.getDownloadURL({
        "scale": study_area.analysis_grid_cell_size_meters,
        "region": rectangle,
        "format": "GEO_TIFF",
        "crs": "EPSG:4326",
    })

    response = requests.get(download_url, timeout=120)
    if response.status_code != 200:
        print("Download failed with HTTP status code " + str(response.status_code) + ". Response text: " + response.text[:300])
        return None

    with open(output_lst_composite_file_path, "wb") as output_file:
        output_file.write(response.content)

    file_size_in_kilobytes = os.path.getsize(output_lst_composite_file_path) / 1024.0
    print("Saved LST composite to " + output_lst_composite_file_path + " (" + str(round(file_size_in_kilobytes, 1)) + " KB)")

    return output_lst_composite_file_path


def validate_downloaded_raster(file_path):
    print("")
    print("Opening the downloaded file with rasterio to confirm it is a real, readable raster with physically plausible values ...")

    if file_path is None or not os.path.exists(file_path):
        print("Validation failed: no file was downloaded.")
        return False, None

    with rasterio.open(file_path) as raster_dataset:
        raster_width_in_pixels = raster_dataset.width
        raster_height_in_pixels = raster_dataset.height
        coordinate_reference_system = raster_dataset.crs
        lst_values = raster_dataset.read(1)

    print("Raster size: " + str(raster_width_in_pixels) + " x " + str(raster_height_in_pixels) + " pixels, CRS: " + str(coordinate_reference_system))

    valid_pixel_count = int((lst_values == lst_values).sum())
    total_pixel_count = lst_values.size
    valid_pixel_percentage = (valid_pixel_count / total_pixel_count) * 100.0
    print("Pixels with a real LST value (not empty/nodata): " + str(round(valid_pixel_percentage, 1)) + "% of the raster")

    minimum_lst_celsius = float(numpy.nanmin(lst_values))
    maximum_lst_celsius = float(numpy.nanmax(lst_values))
    print("LST value range found in the file: " + str(round(minimum_lst_celsius, 1)) + "C to " + str(round(maximum_lst_celsius, 1)) + "C")

    plausible_minimum_celsius = 15.0
    plausible_maximum_celsius = 60.0
    values_are_plausible = minimum_lst_celsius >= plausible_minimum_celsius and maximum_lst_celsius <= plausible_maximum_celsius
    print("LST values fall within the physically plausible " + str(plausible_minimum_celsius) + "C to " + str(plausible_maximum_celsius) + "C range for Chennai summers: " + str(values_are_plausible))

    file_is_valid = valid_pixel_percentage > 50.0 and values_are_plausible
    return file_is_valid, (minimum_lst_celsius, maximum_lst_celsius)


def decide_keep_or_drop_lst(fraction_of_seasons_meeting_minimum, file_is_valid):
    print("")
    print("Deciding whether to KEEP or DROP the LST dataset ...")

    seasons_check_passed = fraction_of_seasons_meeting_minimum >= thresholds.required_fraction_of_summer_seasons_meeting_scene_count
    print("Result: " + str(round(fraction_of_seasons_meeting_minimum * 100, 1)) + "% of seasons had enough cloud-free scenes >= required " + str(round(thresholds.required_fraction_of_summer_seasons_meeting_scene_count * 100, 1)) + "% -> " + ("PASS" if seasons_check_passed else "FAIL"))
    print("Result: downloaded raster passed physical-plausibility validation -> " + ("PASS" if file_is_valid else "FAIL"))

    final_decision_is_keep = seasons_check_passed and file_is_valid

    if final_decision_is_keep:
        print("FINAL DECISION: KEEP the LST dataset.")
    else:
        print("FINAL DECISION: DROP the LST dataset.")
        if not seasons_check_passed:
            print("What is missing: not enough summer seasons have sufficient cloud-free Landsat coverage over this corridor.")
        if not file_is_valid:
            print("What is missing: the downloaded LST raster failed basic physical-plausibility checks.")

    return final_decision_is_keep


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: LST is the numerator (Delta LST) in the Effectiveness Score formula (Delta LST x Vulnerability_weight x Population_affected / Cost). Without it, the Effectiveness Score has no way to measure actual heat reduction and cannot be computed at all.")
    else:
        print("Necessity check skipped because the dataset was dropped. Without LST, the entire Effectiveness Score formula has no numerator and cannot be computed for any cell.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Land Surface Temperature (LST)")
    print("=====================================================")

    landsat_collection, rectangle, season_date_ranges = fetch_landsat_scenes_for_summer_seasons()
    if landsat_collection is None:
        decide_keep_or_drop_lst(0.0, False)
        return

    collection_with_cloud_information, scene_timestamps, scene_cloud_free_percentages = check_cloud_free_percentage_for_each_scene(landsat_collection, rectangle)

    fraction_of_seasons_meeting_minimum, qualifying_scene_count_per_year = summarize_qualifying_scenes_per_summer_season(
        scene_timestamps, scene_cloud_free_percentages, season_date_ranges
    )

    lst_composite_image = build_lst_composite_from_qualifying_scenes(collection_with_cloud_information, rectangle)

    downloaded_file_path = download_lst_composite_to_local_file(lst_composite_image, rectangle)

    file_is_valid, lst_value_range = validate_downloaded_raster(downloaded_file_path)

    final_decision_is_keep = decide_keep_or_drop_lst(fraction_of_seasons_meeting_minimum, file_is_valid)

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        validated_file_path = os.path.join(output_folder_for_validated_data, "lst_150m_composite_celsius.tif")
        with open(downloaded_file_path, "rb") as source_file:
            file_contents = source_file.read()
        with open(validated_file_path, "wb") as destination_file:
            destination_file.write(file_contents)
        print("")
        print("Copied validated LST raster to " + validated_file_path)


if __name__ == "__main__":
    main()
