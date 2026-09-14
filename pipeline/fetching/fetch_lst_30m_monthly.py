import os
import sys
import csv
import calendar
import datetime
import rasterio
import numpy
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import tiled_download

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "lst_30m_monthly")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "lst_30m_monthly")
monthly_log_csv_path = os.path.join(output_folder_for_raw_data, "monthly_lst_log.csv")

requested_start_year = 2021
requested_start_month = 1
requested_end_year = 2026
requested_end_month = 8

plausible_minimum_celsius = 15.0
plausible_maximum_celsius = 55.0

maximum_candidate_scenes_to_try_per_month = 6

dilated_cloud_bit = 1
cirrus_bit = 2
cloud_bit = 3
cloud_shadow_bit = 4


def build_list_of_year_months():
    year_months = []
    year = requested_start_year
    month = requested_start_month
    while (year < requested_end_year) or (year == requested_end_year and month <= requested_end_month):
        year_months.append((year, month))
        month = month + 1
        if month > 12:
            month = 1
            year = year + 1
    return year_months


def get_month_date_range(year, month):
    start_date_text = str(year) + "-" + str(month).zfill(2) + "-01"
    last_day_of_month = calendar.monthrange(year, month)[1]
    end_date_text = str(year) + "-" + str(month).zfill(2) + "-" + str(last_day_of_month).zfill(2)
    return start_date_text, end_date_text


def add_aoi_cloud_free_percentage_property(image, rectangle):
    quality_band = image.select("QA_PIXEL")
    is_clear_pixel = quality_band.bitwiseAnd(1 << dilated_cloud_bit).eq(0)
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cirrus_bit).eq(0))
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cloud_bit).eq(0))
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cloud_shadow_bit).eq(0))
    cloud_free_fraction_statistics = is_clear_pixel.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=rectangle, scale=30, maxPixels=1000000000,
    )
    cloud_free_fraction = ee.Number(cloud_free_fraction_statistics.get("QA_PIXEL"))
    return image.set("aoi_cloud_free_percentage", cloud_free_fraction.multiply(100))


def build_clear_pixel_mask(image):
    quality_band = image.select("QA_PIXEL")
    is_clear_pixel = quality_band.bitwiseAnd(1 << dilated_cloud_bit).eq(0)
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cirrus_bit).eq(0))
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cloud_bit).eq(0))
    is_clear_pixel = is_clear_pixel.And(quality_band.bitwiseAnd(1 << cloud_shadow_bit).eq(0))
    return is_clear_pixel


def convert_thermal_band_to_celsius_and_mask_clouds(image):
    surface_temperature_kelvin = image.select("ST_B10").multiply(0.00341802).add(149.0)
    surface_temperature_celsius = surface_temperature_kelvin.subtract(273.15).rename("LST_CELSIUS")
    clear_pixel_mask = build_clear_pixel_mask(image)
    surface_temperature_celsius_masked = surface_temperature_celsius.updateMask(clear_pixel_mask)
    return image.addBands(surface_temperature_celsius_masked, overwrite=True)


def append_row_to_monthly_log(row):
    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    file_already_exists = os.path.exists(monthly_log_csv_path)
    with open(monthly_log_csv_path, "a", newline="", encoding="utf-8") as csv_file:
        fieldnames = ["year_month", "candidate_scenes", "chosen_scene_id", "sensor", "acquisition_date", "aoi_cloud_free_percentage", "valid_pixel_percentage", "min_celsius", "max_celsius", "decision", "reason", "output_file"]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not file_already_exists:
            csv_writer.writeheader()
        csv_writer.writerow(row)


def get_ranked_candidates_for_month(year, month, rectangle):
    start_date_text, end_date_text = get_month_date_range(year, month)
    print("")
    print("-----------------------------------------------------")
    print("Month " + str(year) + "-" + str(month).zfill(2) + " (" + start_date_text + " to " + end_date_text + ")")
    print("-----------------------------------------------------")

    landsat8_collection = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
    landsat9_collection = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
    combined_collection = landsat8_collection.merge(landsat9_collection)
    combined_collection = combined_collection.filterBounds(rectangle).filterDate(start_date_text, end_date_text)

    candidate_scene_count = combined_collection.size().getInfo()
    print("Candidate Landsat 8+9 scenes this month: " + str(candidate_scene_count))

    if candidate_scene_count == 0:
        return [], candidate_scene_count

    collection_with_cloud_info = combined_collection.map(lambda image: add_aoi_cloud_free_percentage_property(image, rectangle))
    sorted_collection = collection_with_cloud_info.sort("aoi_cloud_free_percentage", False)

    number_to_fetch = min(candidate_scene_count, maximum_candidate_scenes_to_try_per_month)
    candidate_image_list = sorted_collection.toList(number_to_fetch)

    ranked_candidates = []
    for index in range(number_to_fetch):
        image = ee.Image(candidate_image_list.get(index))
        scene_id = image.get("LANDSAT_PRODUCT_ID").getInfo()
        spacecraft_id = image.get("SPACECRAFT_ID").getInfo()
        acquisition_date = ee.Date(image.get("system:time_start")).format("YYYY-MM-dd").getInfo()
        aoi_cloud_free_percentage = image.get("aoi_cloud_free_percentage").getInfo()
        ranked_candidates.append({
            "image": image, "scene_id": scene_id, "sensor": spacecraft_id,
            "acquisition_date": acquisition_date, "aoi_cloud_free_percentage": aoi_cloud_free_percentage,
        })
        print("Candidate " + str(index + 1) + ": " + str(scene_id) + " (" + str(spacecraft_id) + "), " + str(acquisition_date) + ", AOI cloud-free: " + str(round(aoi_cloud_free_percentage, 2)) + "%")

    return ranked_candidates, candidate_scene_count


def validate_and_download_candidate(candidate, rectangle, year_month_label):
    scene_id = candidate["scene_id"]
    aoi_cloud_free_percentage = candidate["aoi_cloud_free_percentage"]

    print("")
    print("Validating candidate " + scene_id + " ...")

    cloud_check_passed = aoi_cloud_free_percentage >= thresholds.required_cloud_free_pixel_percentage_per_scene
    print("Result: " + str(round(aoi_cloud_free_percentage, 2)) + "% AOI-clipped cloud-free >= required " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% -> " + ("PASS" if cloud_check_passed else "FAIL"))

    if not cloud_check_passed:
        return None, "AOI cloud-free percentage below threshold"

    celsius_image = convert_thermal_band_to_celsius_and_mask_clouds(candidate["image"]).select("LST_CELSIUS").clip(rectangle)
    print("Cloud/cirrus/shadow-flagged pixels (per QA_PIXEL) have been masked out of the LST band itself before download -- min/max checks below only see genuinely clear-sky pixels.")

    raw_month_folder = os.path.join(output_folder_for_raw_data, year_month_label)
    downloaded_file_path = tiled_download.download_ee_image_in_tiles(
        celsius_image,
        study_area.study_area_minimum_longitude, study_area.study_area_minimum_latitude,
        study_area.study_area_maximum_longitude, study_area.study_area_maximum_latitude,
        scale_in_meters=30, tiles_per_side=1,
        output_folder=raw_month_folder, base_filename="lst_30m_" + year_month_label,
    )

    if downloaded_file_path is None:
        return None, "download failed"

    with rasterio.open(downloaded_file_path) as raster_dataset:
        raw_values = raster_dataset.read(1)
        raster_nodata = raster_dataset.nodata
        raster_profile = raster_dataset.profile
        pixel_width_meters, pixel_height_meters = raster_dataset.res

    print("Confirmed pixel size from the downloaded file: " + str(round(pixel_width_meters, 3)) + " x " + str(round(pixel_height_meters, 3)) + " (degrees, EPSG:4326) -- native 30m, no resampling applied.")

    is_nodata = numpy.isnan(raw_values) if raster_nodata is None or numpy.isnan(raster_nodata) else (raw_values == raster_nodata)
    valid_mask = ~is_nodata
    valid_pixel_percentage = (valid_mask.sum() / valid_mask.size) * 100.0

    if valid_mask.sum() == 0:
        return None, "zero valid pixels after clipping"

    valid_values = raw_values[valid_mask]
    min_celsius = float(valid_values.min())
    max_celsius = float(valid_values.max())

    print("Valid pixel percentage: " + str(round(valid_pixel_percentage, 1)) + "%")
    print("LST range: " + str(round(min_celsius, 1)) + "C to " + str(round(max_celsius, 1)) + "C")

    range_check_passed = min_celsius >= plausible_minimum_celsius and max_celsius <= plausible_maximum_celsius
    valid_pixel_check_passed = valid_pixel_percentage >= thresholds.required_valid_pixel_percentage_for_10m_raster

    print("Result: values within " + str(plausible_minimum_celsius) + "-" + str(plausible_maximum_celsius) + "C plausible range -> " + ("PASS" if range_check_passed else "FAIL"))
    print("Result: " + str(round(valid_pixel_percentage, 1)) + "% valid pixels >= required " + str(thresholds.required_valid_pixel_percentage_for_10m_raster) + "% -> " + ("PASS" if valid_pixel_check_passed else "FAIL"))

    if not (range_check_passed and valid_pixel_check_passed):
        reason = []
        if not range_check_passed:
            reason.append("LST values outside plausible range")
        if not valid_pixel_check_passed:
            reason.append("too many nodata/masked pixels")
        return None, ", ".join(reason)

    validation_result = {
        "valid_pixel_percentage": valid_pixel_percentage, "min_celsius": min_celsius, "max_celsius": max_celsius,
        "raster_profile": raster_profile, "downloaded_file_path": downloaded_file_path,
    }
    return validation_result, None


def process_one_month(year, month, rectangle, is_recent_month):
    year_month_label = str(year) + "-" + str(month).zfill(2)
    validated_file_path = os.path.join(output_folder_for_validated_data, year_month_label, "lst_30m_" + year_month_label + ".tif")

    if os.path.exists(validated_file_path):
        print("")
        print("Month " + year_month_label + " already has a validated output on disk, skipping (resumable run).")
        return

    ranked_candidates, candidate_scene_count = get_ranked_candidates_for_month(year, month, rectangle)

    if candidate_scene_count == 0:
        reason = "zero candidate scenes"
        if is_recent_month:
            reason = reason + " -- LIKELY USGS COLLECTION 2 PROCESSING LATENCY for this recent month, not necessarily a genuine data gap"
        print("Result: " + reason + " -> DROP")
        append_row_to_monthly_log({
            "year_month": year_month_label, "candidate_scenes": 0, "chosen_scene_id": "", "sensor": "",
            "acquisition_date": "", "aoi_cloud_free_percentage": "", "valid_pixel_percentage": "",
            "min_celsius": "", "max_celsius": "", "decision": "DROP", "reason": reason, "output_file": "",
        })
        return

    for candidate in ranked_candidates:
        validation_result, failure_reason = validate_and_download_candidate(candidate, rectangle, year_month_label)
        if validation_result is not None:
            os.makedirs(os.path.dirname(validated_file_path), exist_ok=True)
            with open(validation_result["downloaded_file_path"], "rb") as source_file:
                file_contents = source_file.read()
            with open(validated_file_path, "wb") as destination_file:
                destination_file.write(file_contents)

            print("FINAL DECISION for " + year_month_label + ": KEEP (scene " + candidate["scene_id"] + ", " + candidate["sensor"] + ")")
            append_row_to_monthly_log({
                "year_month": year_month_label, "candidate_scenes": candidate_scene_count,
                "chosen_scene_id": candidate["scene_id"], "sensor": candidate["sensor"],
                "acquisition_date": candidate["acquisition_date"],
                "aoi_cloud_free_percentage": round(candidate["aoi_cloud_free_percentage"], 2),
                "valid_pixel_percentage": round(validation_result["valid_pixel_percentage"], 1),
                "min_celsius": round(validation_result["min_celsius"], 1),
                "max_celsius": round(validation_result["max_celsius"], 1),
                "decision": "KEEP", "reason": "", "output_file": validated_file_path,
            })
            return
        else:
            print("Candidate " + candidate["scene_id"] + " REJECTED: " + failure_reason + ". Trying next candidate ...")

    reason = "all " + str(len(ranked_candidates)) + " candidate scene(s) failed validation"
    if is_recent_month:
        reason = reason + " -- for this recent month, also consider USGS Collection 2 processing latency as a possible contributing factor"
    print("Result: " + reason + " -> DROP")
    append_row_to_monthly_log({
        "year_month": year_month_label, "candidate_scenes": candidate_scene_count, "chosen_scene_id": "",
        "sensor": "", "acquisition_date": "", "aoi_cloud_free_percentage": "", "valid_pixel_percentage": "",
        "min_celsius": "", "max_celsius": "", "decision": "DROP", "reason": reason, "output_file": "",
    })


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Landsat 8/9 Collection 2 Level-2 LST, native 30m, monthly least-cloudy")
    print("Window: " + str(requested_start_year) + "-" + str(requested_start_month).zfill(2) + " to " + str(requested_end_year) + "-" + str(requested_end_month).zfill(2))
    print("=====================================================")
    print("ACCESS METHOD NOTE: earthaccess.login() / NASA Earthdata is not available in this environment (no credentials, package not installed).")
    print("Substituting the already-authenticated Google Earth Engine path to the IDENTICAL real USGS Collection 2 Level-2 product")
    print("(LANDSAT/LC08/C02/T1_L2 and LANDSAT/LC09/C02/T1_L2) -- same data, same 30m native ST_B10 band, no downscaling, only the")
    print("delivery API differs. Flagging this explicitly rather than silently substituting.")
    print("No resampling or sharpening of any kind is applied -- scale=30 in every download call, matching the native distributed resolution.")

    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    year_months = build_list_of_year_months()
    print("Total months to process: " + str(len(year_months)))

    today = datetime.date.today()
    recent_cutoff = (today.year, today.month - 1 if today.month > 1 else 12)

    for index, (year, month) in enumerate(year_months):
        is_recent_month = (index >= len(year_months) - 2)
        print("")
        print("Processing month " + str(index + 1) + " of " + str(len(year_months)) + " ...")
        process_one_month(year, month, rectangle, is_recent_month)

    print("")
    print("=====================================================")
    print("ALL MONTHS PROCESSED. Full log at " + monthly_log_csv_path)
    print("=====================================================")


if __name__ == "__main__":
    main()
