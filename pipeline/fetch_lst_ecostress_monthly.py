import os
import sys
import csv
import calendar
import requests
import rasterio
import numpy
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "lst_70m_ecostress_monthly")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "lst_70m_ecostress_monthly")
monthly_log_csv_path = os.path.join(output_folder_for_raw_data, "monthly_ecostress_log.csv")

requested_start_year = 2021
requested_start_month = 1
requested_end_year = 2026
requested_end_month = 8

native_resolution_meters = 70
plausible_minimum_celsius = 15.0
plausible_maximum_celsius = 55.0
maximum_candidate_scenes_to_try_per_month = 8


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
    lst_band_present = image.select("LST").mask()
    cloud_band = image.select("cloud")
    is_clear_pixel = cloud_band.eq(0)
    is_present_and_clear = lst_band_present.And(is_clear_pixel).unmask(0)
    coverage_statistics = is_present_and_clear.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=rectangle, scale=native_resolution_meters, maxPixels=1000000000, bestEffort=True,
    )
    clear_fraction = ee.Number(coverage_statistics.get("LST"))
    return image.set("aoi_cloud_free_percentage", ee.Algorithms.If(clear_fraction, clear_fraction.multiply(100), 0))


def append_row_to_monthly_log(row):
    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    file_already_exists = os.path.exists(monthly_log_csv_path)
    with open(monthly_log_csv_path, "a", newline="", encoding="utf-8") as csv_file:
        fieldnames = ["year_month", "candidate_scenes", "chosen_scene_id", "acquisition_datetime", "aoi_cloud_free_percentage", "valid_pixel_percentage", "min_celsius", "max_celsius", "decision", "reason", "output_file"]
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

    collection = ee.ImageCollection("NASA/ECOSTRESS/L2T_LSTE/V2")
    collection = collection.filterBounds(rectangle).filterDate(start_date_text, end_date_text)

    candidate_scene_count = collection.size().getInfo()
    print("Candidate ECOSTRESS scenes this month: " + str(candidate_scene_count))

    if candidate_scene_count == 0:
        return [], candidate_scene_count

    collection_with_cloud_info = collection.map(lambda image: add_aoi_cloud_free_percentage_property(image, rectangle))
    sorted_collection = collection_with_cloud_info.sort("aoi_cloud_free_percentage", False)

    number_to_fetch = min(candidate_scene_count, maximum_candidate_scenes_to_try_per_month)
    candidate_image_list = sorted_collection.toList(number_to_fetch)

    ranked_candidates = []
    for index in range(number_to_fetch):
        image = ee.Image(candidate_image_list.get(index))
        scene_id = image.get("system:index").getInfo()
        acquisition_datetime = ee.Date(image.get("system:time_start")).format("YYYY-MM-dd HH:mm").getInfo()
        aoi_cloud_free_percentage = image.get("aoi_cloud_free_percentage").getInfo()
        ranked_candidates.append({
            "image": image, "scene_id": scene_id,
            "acquisition_datetime": acquisition_datetime, "aoi_cloud_free_percentage": aoi_cloud_free_percentage,
        })
        print("Candidate " + str(index + 1) + ": " + str(scene_id) + ", " + str(acquisition_datetime) + ", AOI cloud-free: " + str(round(aoi_cloud_free_percentage, 2)) + "%")

    return ranked_candidates, candidate_scene_count


def download_native_utm_image(image, rectangle, output_folder, base_filename):
    os.makedirs(output_folder, exist_ok=True)
    native_projection = image.select("LST").projection()
    native_crs = native_projection.crs().getInfo()

    download_url = image.select("LST").getDownloadURL({
        "scale": native_resolution_meters,
        "region": rectangle,
        "format": "GEO_TIFF",
        "crs": native_crs,
    })

    response = requests.get(download_url, timeout=180)
    if response.status_code != 200:
        print("Download failed: HTTP " + str(response.status_code) + " -- " + response.text[:200])
        return None, native_crs

    file_path = os.path.join(output_folder, base_filename + ".tif")
    with open(file_path, "wb") as output_file:
        output_file.write(response.content)

    print("Downloaded in native CRS " + native_crs + " at " + str(native_resolution_meters) + "m (no reprojection/resampling), saved to " + file_path)
    return file_path, native_crs


def validate_and_download_candidate(candidate, rectangle, year_month_label):
    scene_id = candidate["scene_id"]
    aoi_cloud_free_percentage = candidate["aoi_cloud_free_percentage"]

    print("")
    print("Validating candidate " + scene_id + " ...")

    cloud_check_passed = aoi_cloud_free_percentage >= thresholds.required_cloud_free_pixel_percentage_per_scene
    print("Result: " + str(round(aoi_cloud_free_percentage, 2)) + "% AOI-clipped cloud-free >= required " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% -> " + ("PASS" if cloud_check_passed else "FAIL"))

    if not cloud_check_passed:
        return None, "AOI cloud-free percentage below threshold"

    lst_celsius_image = candidate["image"].select("LST").subtract(273.15)
    cloud_mask = candidate["image"].select("cloud").eq(0)
    lst_celsius_masked = lst_celsius_image.updateMask(cloud_mask).clip(rectangle)

    raw_month_folder = os.path.join(output_folder_for_raw_data, year_month_label)
    downloaded_file_path, native_crs = download_native_utm_image(lst_celsius_masked, rectangle, raw_month_folder, "lst_70m_ecostress_" + year_month_label)

    if downloaded_file_path is None:
        return None, "download failed"

    with rasterio.open(downloaded_file_path) as raster_dataset:
        raw_values = raster_dataset.read(1)
        raster_nodata = raster_dataset.nodata
        pixel_width_meters, pixel_height_meters = raster_dataset.res
        raster_crs = raster_dataset.crs

    print("Confirmed pixel size from the downloaded file: " + str(round(pixel_width_meters, 2)) + " x " + str(round(pixel_height_meters, 2)) + " meters (CRS: " + str(raster_crs) + ") -- native 70m, no resampling applied.")

    is_nodata = numpy.isnan(raw_values) if raster_nodata is None or numpy.isnan(raster_nodata) else (raw_values == raster_nodata)
    valid_mask = ~is_nodata
    valid_pixel_percentage = (valid_mask.sum() / valid_mask.size) * 100.0

    if valid_mask.sum() == 0:
        return None, "zero valid pixels after clipping and cloud masking"

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

    return {
        "valid_pixel_percentage": valid_pixel_percentage, "min_celsius": min_celsius, "max_celsius": max_celsius,
        "downloaded_file_path": downloaded_file_path,
    }, None


def process_one_month(year, month, rectangle):
    year_month_label = str(year) + "-" + str(month).zfill(2)
    validated_file_path = os.path.join(output_folder_for_validated_data, year_month_label, "lst_70m_ecostress_" + year_month_label + ".tif")

    if os.path.exists(validated_file_path):
        print("")
        print("Month " + year_month_label + " already has a validated output on disk, skipping (resumable run).")
        return

    ranked_candidates, candidate_scene_count = get_ranked_candidates_for_month(year, month, rectangle)

    if candidate_scene_count == 0:
        print("Result: zero candidate scenes -> DROP")
        append_row_to_monthly_log({
            "year_month": year_month_label, "candidate_scenes": 0, "chosen_scene_id": "", "acquisition_datetime": "",
            "aoi_cloud_free_percentage": "", "valid_pixel_percentage": "", "min_celsius": "", "max_celsius": "",
            "decision": "DROP", "reason": "zero candidate scenes", "output_file": "",
        })
        return

    for candidate in ranked_candidates:
        try:
            validation_result, failure_reason = validate_and_download_candidate(candidate, rectangle, year_month_label)
        except Exception as error:
            print("Candidate " + candidate["scene_id"] + " ERRORED: " + type(error).__name__ + ": " + str(error))
            continue

        if validation_result is not None:
            os.makedirs(os.path.dirname(validated_file_path), exist_ok=True)
            with open(validation_result["downloaded_file_path"], "rb") as source_file:
                file_contents = source_file.read()
            with open(validated_file_path, "wb") as destination_file:
                destination_file.write(file_contents)

            print("FINAL DECISION for " + year_month_label + ": KEEP (scene " + candidate["scene_id"] + ")")
            append_row_to_monthly_log({
                "year_month": year_month_label, "candidate_scenes": candidate_scene_count,
                "chosen_scene_id": candidate["scene_id"], "acquisition_datetime": candidate["acquisition_datetime"],
                "aoi_cloud_free_percentage": round(candidate["aoi_cloud_free_percentage"], 2),
                "valid_pixel_percentage": round(validation_result["valid_pixel_percentage"], 1),
                "min_celsius": round(validation_result["min_celsius"], 1),
                "max_celsius": round(validation_result["max_celsius"], 1),
                "decision": "KEEP", "reason": "", "output_file": validated_file_path,
            })
            return
        else:
            print("Candidate " + candidate["scene_id"] + " REJECTED: " + str(failure_reason) + ". Trying next candidate ...")

    reason = "all " + str(len(ranked_candidates)) + " candidate scene(s) failed validation"
    print("Result: " + reason + " -> DROP")
    append_row_to_monthly_log({
        "year_month": year_month_label, "candidate_scenes": candidate_scene_count, "chosen_scene_id": "",
        "acquisition_datetime": "", "aoi_cloud_free_percentage": "", "valid_pixel_percentage": "",
        "min_celsius": "", "max_celsius": "", "decision": "DROP", "reason": reason, "output_file": "",
    })


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: ECOSTRESS L2T LSTE, native 70m, monthly least-cloudy")
    print("Window: " + str(requested_start_year) + "-" + str(requested_start_month).zfill(2) + " to " + str(requested_end_year) + "-" + str(requested_end_month).zfill(2))
    print("=====================================================")
    print("ACCESS NOTE: this is the project's originally-specified PRIMARY LST source. It is free, requires no signup,")
    print("and is fetched through the same already-authenticated Google Earth Engine connection (asset NASA/ECOSTRESS/L2T_LSTE/V2)")
    print("-- no NASA Earthdata/AppEEARS account is needed after all. Native resolution 70m, confirmed via the image's own projection metadata.")
    print("Downloaded in each scene's native UTM CRS at 70m -- no reprojection or resampling of any kind.")
    print("This is a SEPARATE dataset from the 30m Landsat LST track, not merged with it, to avoid mixing two different native resolutions silently.")

    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    year_months = build_list_of_year_months()
    print("Total months to process: " + str(len(year_months)))

    for index, (year, month) in enumerate(year_months):
        print("")
        print("Processing month " + str(index + 1) + " of " + str(len(year_months)) + " ...")
        process_one_month(year, month, rectangle)

    print("")
    print("=====================================================")
    print("ALL MONTHS PROCESSED. Full log at " + monthly_log_csv_path)
    print("=====================================================")


if __name__ == "__main__":
    main()
