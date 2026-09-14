import os
import sys
import csv
import datetime
import calendar
import rasterio
import numpy
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import sentinel2_composite
from pipeline.common import tiled_download

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "sentinel2_10m_monthly")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "sentinel2_10m_monthly")
monthly_log_csv_path = os.path.join(output_folder_for_raw_data, "monthly_fetch_log.csv")

requested_start_year = 2021
requested_start_month = 1
requested_end_year = 2026
requested_end_month = 8

native_10m_band_names = ["B2", "B3", "B4", "B8"]
tiles_per_side_for_download = 4

minimum_qualifying_scenes_per_month = 1
sparse_month_scene_count_warning_level = 3


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
        fieldnames = ["year_month", "candidate_scenes", "qualifying_scenes", "decision", "valid_pixel_percentage", "ndvi_min", "ndvi_max", "output_file"]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        if not file_already_exists:
            csv_writer.writeheader()
        csv_writer.writerow(row)


def fetch_month_composite(year, month, rectangle):
    start_date_text, end_date_text = get_month_date_range(year, month)
    print("")
    print("-----------------------------------------------------")
    print("Month " + str(year) + "-" + str(month).zfill(2) + " (" + start_date_text + " to " + end_date_text + ")")
    print("-----------------------------------------------------")

    sentinel2_collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    sentinel2_collection = sentinel2_collection.filterBounds(rectangle)
    sentinel2_collection = sentinel2_collection.filterDate(start_date_text, end_date_text)

    candidate_scene_count = sentinel2_collection.size().getInfo()
    print("Candidate Sentinel-2 scenes this month: " + str(candidate_scene_count))

    if candidate_scene_count == 0:
        print("Result: 0 candidate scenes -> DROP for this month.")
        return None, candidate_scene_count, 0

    collection_with_cloud_information = sentinel2_collection.map(
        lambda image: sentinel2_composite.add_cloud_free_percentage_property(image, rectangle)
    )
    qualifying_collection = collection_with_cloud_information.filter(
        ee.Filter.gte("cloud_free_percentage", thresholds.required_cloud_free_pixel_percentage_per_scene)
    )
    qualifying_scene_count = qualifying_collection.size().getInfo()
    print("Scenes passing the " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% cloud-free threshold: " + str(qualifying_scene_count))

    if qualifying_scene_count < minimum_qualifying_scenes_per_month:
        print("Result: " + str(qualifying_scene_count) + " qualifying scenes < required minimum " + str(minimum_qualifying_scenes_per_month) + " -> DROP for this month.")
        return None, candidate_scene_count, qualifying_scene_count

    if qualifying_scene_count < sparse_month_scene_count_warning_level:
        print("Warning: only " + str(qualifying_scene_count) + " qualifying scene(s) this month -- composite will be built, but it is thin and less reliable than a normal month.")

    composite_image = qualifying_collection.select(native_10m_band_names).median().clip(rectangle)
    print("Built median composite for " + str(year) + "-" + str(month).zfill(2) + " from " + str(qualifying_scene_count) + " qualifying scene(s).")

    return composite_image, candidate_scene_count, qualifying_scene_count


def validate_month_raster(file_path):
    if file_path is None or not os.path.exists(file_path):
        return False, None, None, None

    with rasterio.open(file_path) as raster_dataset:
        band_values = raster_dataset.read()

    valid_pixel_mask = ~numpy.isnan(band_values).any(axis=0)
    valid_pixel_percentage = (valid_pixel_mask.sum() / valid_pixel_mask.size) * 100.0

    red_band = band_values[2]
    nir_band = band_values[3]
    with numpy.errstate(invalid="ignore", divide="ignore"):
        ndvi_values = (nir_band - red_band) / (nir_band + red_band)
    valid_ndvi_values = ndvi_values[~numpy.isnan(ndvi_values)]

    ndvi_min = float(valid_ndvi_values.min()) if len(valid_ndvi_values) > 0 else None
    ndvi_max = float(valid_ndvi_values.max()) if len(valid_ndvi_values) > 0 else None
    ndvi_in_valid_range = ndvi_min is not None and ndvi_min >= -1.0001 and ndvi_max <= 1.0001

    print("Valid pixel percentage: " + str(round(valid_pixel_percentage, 1)) + "%, NDVI range: " + str(round(ndvi_min, 3) if ndvi_min is not None else None) + " to " + str(round(ndvi_max, 3) if ndvi_max is not None else None))

    file_is_valid = valid_pixel_percentage >= thresholds.required_valid_pixel_percentage_for_10m_raster and ndvi_in_valid_range
    print("Result: " + str(round(valid_pixel_percentage, 1)) + "% valid pixels >= required " + str(thresholds.required_valid_pixel_percentage_for_10m_raster) + "%, NDVI physically valid: " + str(ndvi_in_valid_range) + " -> " + ("PASS" if file_is_valid else "FAIL"))

    return file_is_valid, valid_pixel_percentage, ndvi_min, ndvi_max


def process_one_month(year, month, rectangle):
    year_month_label = str(year) + "-" + str(month).zfill(2)
    validated_file_path = os.path.join(output_folder_for_validated_data, year_month_label, "sentinel2_10m_" + year_month_label + ".tif")

    if os.path.exists(validated_file_path):
        print("")
        print("Month " + year_month_label + " already has a validated output on disk, skipping (resumable run).")
        return

    try:
        composite_image, candidate_scene_count, qualifying_scene_count = fetch_month_composite(year, month, rectangle)

        if composite_image is None:
            append_row_to_monthly_log({
                "year_month": year_month_label,
                "candidate_scenes": candidate_scene_count,
                "qualifying_scenes": qualifying_scene_count,
                "decision": "DROP",
                "valid_pixel_percentage": "",
                "ndvi_min": "",
                "ndvi_max": "",
                "output_file": "",
            })
            return

        raw_month_folder = os.path.join(output_folder_for_raw_data, year_month_label)
        downloaded_file_path = tiled_download.download_ee_image_in_tiles(
            composite_image,
            study_area.study_area_minimum_longitude,
            study_area.study_area_minimum_latitude,
            study_area.study_area_maximum_longitude,
            study_area.study_area_maximum_latitude,
            scale_in_meters=10,
            tiles_per_side=tiles_per_side_for_download,
            output_folder=raw_month_folder,
            base_filename="sentinel2_10m_" + year_month_label,
        )

        file_is_valid, valid_pixel_percentage, ndvi_min, ndvi_max = validate_month_raster(downloaded_file_path)

        decision_text = "KEEP" if file_is_valid else "DROP"
        print("FINAL DECISION for " + year_month_label + ": " + decision_text)

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
            "year_month": year_month_label,
            "candidate_scenes": candidate_scene_count,
            "qualifying_scenes": qualifying_scene_count,
            "decision": decision_text,
            "valid_pixel_percentage": round(valid_pixel_percentage, 1) if valid_pixel_percentage is not None else "",
            "ndvi_min": round(ndvi_min, 3) if ndvi_min is not None else "",
            "ndvi_max": round(ndvi_max, 3) if ndvi_max is not None else "",
            "output_file": output_file_for_log,
        })

    except Exception as error:
        print("ERROR while processing " + year_month_label + ": " + type(error).__name__ + ": " + str(error))
        append_row_to_monthly_log({
            "year_month": year_month_label,
            "candidate_scenes": "",
            "qualifying_scenes": "",
            "decision": "ERROR: " + type(error).__name__,
            "valid_pixel_percentage": "",
            "ndvi_min": "",
            "ndvi_max": "",
            "output_file": "",
        })


def main():
    print("=====================================================")
    print("FETCHING NATIVE-10m SENTINEL-2 BANDS, ONE COMPOSITE PER CALENDAR MONTH")
    print("Window: " + str(requested_start_year) + "-" + str(requested_start_month).zfill(2) + " to " + str(requested_end_year) + "-" + str(requested_end_month).zfill(2))
    print("=====================================================")
    print("This is a long-running job by design (one fetch + cloud-check + 16-tile download per month). Progress is logged per month to:")
    print(monthly_log_csv_path)
    print("The run is resumable: months that already have a validated file on disk are skipped if restarted.")

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
