import os
import sys
import csv
import calendar
import rasterio
import numpy
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import sentinel2_composite
from pipeline.common import tiled_download

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "sentinel2_10m_monthly_least_cloudy")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "sentinel2_10m_monthly_least_cloudy")
monthly_log_csv_path = os.path.join(output_folder_for_raw_data, "monthly_least_cloudy_log.csv")

requested_start_year = 2021
requested_start_month = 1
requested_end_year = 2026
requested_end_month = 8

native_10m_band_names = ["B2", "B3", "B4", "B8"]
tiles_per_side_for_download = 2

mgrs_tile_that_fully_covers_this_aoi = "44PMV"


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


def append_row_to_monthly_log(row):
    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    file_already_exists = os.path.exists(monthly_log_csv_path)
    with open(monthly_log_csv_path, "a", newline="", encoding="utf-8") as csv_file:
        fieldnames = ["year_month", "candidate_scenes", "chosen_scene_id", "acquisition_date", "metadata_cloud_percentage", "computed_cloud_free_percentage", "decision", "valid_pixel_percentage", "ndvi_min", "ndvi_max", "output_file"]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not file_already_exists:
            csv_writer.writeheader()
        csv_writer.writerow(row)


def find_least_cloudy_scene_for_month(year, month, rectangle):
    start_date_text, end_date_text = get_month_date_range(year, month)
    print("")
    print("-----------------------------------------------------")
    print("Month " + str(year) + "-" + str(month).zfill(2) + " (" + start_date_text + " to " + end_date_text + ")")
    print("-----------------------------------------------------")

    sentinel2_collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    sentinel2_collection = sentinel2_collection.filterBounds(rectangle)
    sentinel2_collection = sentinel2_collection.filterDate(start_date_text, end_date_text)
    sentinel2_collection = sentinel2_collection.filter(ee.Filter.eq("MGRS_TILE", mgrs_tile_that_fully_covers_this_aoi))

    candidate_scene_count = sentinel2_collection.size().getInfo()
    print("Candidate Sentinel-2 scenes this month (restricted to tile " + mgrs_tile_that_fully_covers_this_aoi + ", the only tile confirmed to fully cover this AOI): " + str(candidate_scene_count))

    if candidate_scene_count == 0:
        print("Result: 0 candidate scenes -> DROP for this month.")
        return None, candidate_scene_count, None, None, None

    sorted_collection = sentinel2_collection.sort("CLOUDY_PIXEL_PERCENTAGE")
    least_cloudy_image = ee.Image(sorted_collection.first())

    scene_id = least_cloudy_image.get("PRODUCT_ID").getInfo()
    acquisition_date = ee.Date(least_cloudy_image.get("system:time_start")).format("YYYY-MM-dd").getInfo()
    metadata_cloud_percentage = least_cloudy_image.get("CLOUDY_PIXEL_PERCENTAGE").getInfo()

    print("Least-cloudy scene per Sentinel-2's own metadata: " + str(scene_id))
    print("Acquisition date: " + str(acquisition_date) + ", metadata CLOUDY_PIXEL_PERCENTAGE: " + str(metadata_cloud_percentage) + "%")

    return least_cloudy_image, candidate_scene_count, scene_id, acquisition_date, metadata_cloud_percentage


def independently_verify_cloud_free_percentage(image, rectangle):
    print("Independently computing real cloud-free percentage from the SCL band (not just trusting the metadata tag) ...")
    image_with_cloud_info = sentinel2_composite.add_cloud_free_percentage_property(image, rectangle)
    computed_cloud_free_percentage = image_with_cloud_info.get("cloud_free_percentage").getInfo()
    print("Independently computed cloud-free percentage over the study area: " + str(round(computed_cloud_free_percentage, 2)) + "%")
    return computed_cloud_free_percentage


def validate_month_raster(file_path):
    if file_path is None or not os.path.exists(file_path):
        return False, None, None, None

    with rasterio.open(file_path) as raster_dataset:
        raw_band_values = raster_dataset.read()
        raster_nodata_value = raster_dataset.nodata

    print("Raw downloaded data type: " + str(raw_band_values.dtype) + ", file-reported nodata value: " + str(raster_nodata_value))

    band_values = raw_band_values.astype(numpy.float64)

    if raster_nodata_value is not None:
        is_nodata_pixel = numpy.any(raw_band_values == raster_nodata_value, axis=0)
    else:
        is_nodata_pixel = numpy.any(raw_band_values == 0, axis=0)

    valid_pixel_mask = ~is_nodata_pixel
    valid_pixel_percentage = (valid_pixel_mask.sum() / valid_pixel_mask.size) * 100.0

    red_band = band_values[2]
    nir_band = band_values[3]
    with numpy.errstate(invalid="ignore", divide="ignore"):
        ndvi_values = (nir_band - red_band) / (nir_band + red_band)
    ndvi_values_at_valid_pixels = ndvi_values[valid_pixel_mask]
    ndvi_values_at_valid_pixels = ndvi_values_at_valid_pixels[~numpy.isnan(ndvi_values_at_valid_pixels)]

    ndvi_min = float(ndvi_values_at_valid_pixels.min()) if len(ndvi_values_at_valid_pixels) > 0 else None
    ndvi_max = float(ndvi_values_at_valid_pixels.max()) if len(ndvi_values_at_valid_pixels) > 0 else None
    ndvi_in_valid_range = ndvi_min is not None and ndvi_min >= -1.0001 and ndvi_max <= 1.0001

    print("Valid pixel percentage (based on the raster's real nodata value, not NaN): " + str(round(valid_pixel_percentage, 1)) + "%, NDVI range at valid pixels: " + str(round(ndvi_min, 3) if ndvi_min is not None else None) + " to " + str(round(ndvi_max, 3) if ndvi_max is not None else None))

    file_is_valid = valid_pixel_percentage >= thresholds.required_valid_pixel_percentage_for_10m_raster and ndvi_in_valid_range
    print("Result: " + str(round(valid_pixel_percentage, 1)) + "% valid pixels >= required " + str(thresholds.required_valid_pixel_percentage_for_10m_raster) + "%, NDVI physically valid: " + str(ndvi_in_valid_range) + " -> " + ("PASS" if file_is_valid else "FAIL"))

    return file_is_valid, valid_pixel_percentage, ndvi_min, ndvi_max


def process_one_month(year, month, rectangle):
    year_month_label = str(year) + "-" + str(month).zfill(2)
    validated_file_path = os.path.join(output_folder_for_validated_data, year_month_label, "sentinel2_10m_least_cloudy_" + year_month_label + ".tif")

    if os.path.exists(validated_file_path):
        print("")
        print("Month " + year_month_label + " already has a validated output on disk, skipping (resumable run).")
        return

    try:
        least_cloudy_image, candidate_scene_count, scene_id, acquisition_date, metadata_cloud_percentage = find_least_cloudy_scene_for_month(year, month, rectangle)

        if least_cloudy_image is None:
            append_row_to_monthly_log({
                "year_month": year_month_label, "candidate_scenes": candidate_scene_count, "chosen_scene_id": "",
                "acquisition_date": "", "metadata_cloud_percentage": "", "computed_cloud_free_percentage": "",
                "decision": "DROP", "valid_pixel_percentage": "", "ndvi_min": "", "ndvi_max": "", "output_file": "",
            })
            return

        computed_cloud_free_percentage = independently_verify_cloud_free_percentage(least_cloudy_image, rectangle)

        cloud_check_passed = computed_cloud_free_percentage >= thresholds.required_cloud_free_pixel_percentage_per_scene
        print("Result: " + str(round(computed_cloud_free_percentage, 2)) + "% cloud-free >= required " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% -> " + ("PASS" if cloud_check_passed else "FAIL"))

        if not cloud_check_passed:
            print("FINAL DECISION for " + year_month_label + ": DROP -- even the least-cloudy scene this month is too cloudy over the study area.")
            append_row_to_monthly_log({
                "year_month": year_month_label, "candidate_scenes": candidate_scene_count, "chosen_scene_id": scene_id,
                "acquisition_date": acquisition_date, "metadata_cloud_percentage": metadata_cloud_percentage,
                "computed_cloud_free_percentage": round(computed_cloud_free_percentage, 2),
                "decision": "DROP", "valid_pixel_percentage": "", "ndvi_min": "", "ndvi_max": "", "output_file": "",
            })
            return

        clipped_image = least_cloudy_image.select(native_10m_band_names).clip(rectangle)

        raw_month_folder = os.path.join(output_folder_for_raw_data, year_month_label)
        downloaded_file_path = tiled_download.download_ee_image_in_tiles(
            clipped_image,
            study_area.study_area_minimum_longitude,
            study_area.study_area_minimum_latitude,
            study_area.study_area_maximum_longitude,
            study_area.study_area_maximum_latitude,
            scale_in_meters=10,
            tiles_per_side=tiles_per_side_for_download,
            output_folder=raw_month_folder,
            base_filename="sentinel2_10m_least_cloudy_" + year_month_label,
        )

        file_is_valid, valid_pixel_percentage, ndvi_min, ndvi_max = validate_month_raster(downloaded_file_path)

        decision_text = "KEEP" if file_is_valid else "DROP"
        print("FINAL DECISION for " + year_month_label + ": " + decision_text + " (scene " + str(scene_id) + ")")

        output_file_for_log = ""
        if file_is_valid:
            os.makedirs(os.path.dirname(validated_file_path), exist_ok=True)
            with open(downloaded_file_path, "rb") as source_file:
                file_contents = source_file.read()
            with open(validated_file_path, "wb") as destination_file:
                destination_file.write(file_contents)
            print("Saved validated raster to " + validated_file_path)
            output_file_for_log = validated_file_path

        append_row_to_monthly_log({
            "year_month": year_month_label, "candidate_scenes": candidate_scene_count, "chosen_scene_id": scene_id,
            "acquisition_date": acquisition_date, "metadata_cloud_percentage": metadata_cloud_percentage,
            "computed_cloud_free_percentage": round(computed_cloud_free_percentage, 2),
            "decision": decision_text,
            "valid_pixel_percentage": round(valid_pixel_percentage, 1) if valid_pixel_percentage is not None else "",
            "ndvi_min": round(ndvi_min, 3) if ndvi_min is not None else "",
            "ndvi_max": round(ndvi_max, 3) if ndvi_max is not None else "",
            "output_file": output_file_for_log,
        })

    except Exception as error:
        print("ERROR while processing " + year_month_label + ": " + type(error).__name__ + ": " + str(error))
        append_row_to_monthly_log({
            "year_month": year_month_label, "candidate_scenes": "", "chosen_scene_id": "",
            "acquisition_date": "", "metadata_cloud_percentage": "", "computed_cloud_free_percentage": "",
            "decision": "ERROR: " + type(error).__name__, "valid_pixel_percentage": "", "ndvi_min": "", "ndvi_max": "", "output_file": "",
        })


def main():
    print("=====================================================")
    print("FETCHING NATIVE-10m SENTINEL-2 BANDS: SINGLE LEAST-CLOUDY SCENE PER CALENDAR MONTH")
    print("Window: " + str(requested_start_year) + "-" + str(requested_start_month).zfill(2) + " to " + str(requested_end_year) + "-" + str(requested_end_month).zfill(2))
    print("=====================================================")
    print("Difference from the earlier monthly approach: this picks ONE real, single-date scene per month (the least cloudy available), not a multi-scene median blend. Every pixel in the output comes from one real acquisition.")
    print("Progress logged per month to: " + monthly_log_csv_path)
    print("Resumable: months with an existing validated file are skipped if restarted.")

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
