import os
import sys
import csv
import rasterio
import numpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.common import thresholds

source_monthly_bands_folder = os.path.join("D:\\", "Projects", "UC", "data", "validated", "sentinel2_10m_monthly_least_cloudy")

output_ndvi_folder = os.path.join("D:\\", "Projects", "UC", "data", "validated", "ndvi_10m_monthly")
output_ndwi_folder = os.path.join("D:\\", "Projects", "UC", "data", "validated", "water_ndwi_10m_monthly")

log_csv_path = os.path.join("D:\\", "Projects", "UC", "data", "raw", "ndvi_ndwi_10m_monthly_log.csv")


def find_all_monthly_band_files():
    print("Looking for already-fetched, already-validated monthly 10m band files ...")
    month_folders = sorted(os.listdir(source_monthly_bands_folder))

    found_files = []
    for month_label in month_folders:
        month_folder_path = os.path.join(source_monthly_bands_folder, month_label)
        if not os.path.isdir(month_folder_path):
            continue
        expected_file_path = os.path.join(month_folder_path, "sentinel2_10m_least_cloudy_" + month_label + ".tif")
        if os.path.exists(expected_file_path):
            found_files.append((month_label, expected_file_path))

    print("Found " + str(len(found_files)) + " monthly band files to derive NDVI and NDWI from.")
    return found_files


def compute_ndvi_and_ndwi_for_one_month(month_label, band_file_path):
    print("")
    print("-----------------------------------------------------")
    print("Deriving NDVI and NDWI for " + month_label + " from " + band_file_path)
    print("-----------------------------------------------------")

    with rasterio.open(band_file_path) as raster_dataset:
        raw_band_values = raster_dataset.read()
        raster_profile = raster_dataset.profile
        raster_nodata_value = raster_dataset.nodata

    band_values = raw_band_values.astype(numpy.float64)

    if raster_nodata_value is not None:
        is_nodata_pixel = numpy.any(raw_band_values == raster_nodata_value, axis=0)
    else:
        is_nodata_pixel = numpy.any(raw_band_values == 0, axis=0)
    valid_pixel_mask = ~is_nodata_pixel

    blue_band = band_values[0]
    green_band = band_values[1]
    red_band = band_values[2]
    nir_band = band_values[3]

    with numpy.errstate(invalid="ignore", divide="ignore"):
        ndvi_array = (nir_band - red_band) / (nir_band + red_band)
        ndwi_array = (green_band - nir_band) / (green_band + nir_band)

    ndvi_array[~valid_pixel_mask] = numpy.nan
    ndwi_array[~valid_pixel_mask] = numpy.nan

    ndvi_valid_values = ndvi_array[valid_pixel_mask]
    ndvi_valid_values = ndvi_valid_values[~numpy.isnan(ndvi_valid_values)]
    ndvi_min = float(ndvi_valid_values.min()) if len(ndvi_valid_values) > 0 else None
    ndvi_max = float(ndvi_valid_values.max()) if len(ndvi_valid_values) > 0 else None

    ndwi_valid_values = ndwi_array[valid_pixel_mask]
    ndwi_valid_values = ndwi_valid_values[~numpy.isnan(ndwi_valid_values)]
    ndwi_min = float(ndwi_valid_values.min()) if len(ndwi_valid_values) > 0 else None
    ndwi_max = float(ndwi_valid_values.max()) if len(ndwi_valid_values) > 0 else None

    print("NDVI range: " + str(round(ndvi_min, 3) if ndvi_min is not None else None) + " to " + str(round(ndvi_max, 3) if ndvi_max is not None else None))
    print("NDWI range: " + str(round(ndwi_min, 3) if ndwi_min is not None else None) + " to " + str(round(ndwi_max, 3) if ndwi_max is not None else None))

    ndvi_in_valid_range = ndvi_min is not None and ndvi_min >= -1.0001 and ndvi_max <= 1.0001
    ndwi_in_valid_range = ndwi_min is not None and ndwi_min >= -1.0001 and ndwi_max <= 1.0001
    both_indices_valid = ndvi_in_valid_range and ndwi_in_valid_range

    print("Result: NDVI physically valid: " + str(ndvi_in_valid_range) + ", NDWI physically valid: " + str(ndwi_in_valid_range) + " -> " + ("PASS" if both_indices_valid else "FAIL"))

    if not both_indices_valid:
        print("DROP: computed indices for " + month_label + " failed the physical-range check, not saving.")
        return {
            "year_month": month_label, "ndvi_min": "", "ndvi_max": "", "ndwi_min": "", "ndwi_max": "",
            "decision": "DROP", "ndvi_file": "", "ndwi_file": "",
        }

    os.makedirs(output_ndvi_folder, exist_ok=True)
    os.makedirs(output_ndwi_folder, exist_ok=True)

    ndvi_output_path = os.path.join(output_ndvi_folder, "ndvi_10m_" + month_label + ".tif")
    ndwi_output_path = os.path.join(output_ndwi_folder, "ndwi_10m_" + month_label + ".tif")

    single_band_profile = raster_profile.copy()
    single_band_profile.update(count=1, dtype="float32", nodata=float("nan"))

    with rasterio.open(ndvi_output_path, "w", **single_band_profile) as output_raster:
        output_raster.write(ndvi_array.astype(numpy.float32), 1)

    with rasterio.open(ndwi_output_path, "w", **single_band_profile) as output_raster:
        output_raster.write(ndwi_array.astype(numpy.float32), 1)

    print("Saved NDVI to " + ndvi_output_path)
    print("Saved NDWI to " + ndwi_output_path)

    return {
        "year_month": month_label,
        "ndvi_min": round(ndvi_min, 3), "ndvi_max": round(ndvi_max, 3),
        "ndwi_min": round(ndwi_min, 3), "ndwi_max": round(ndwi_max, 3),
        "decision": "KEEP", "ndvi_file": ndvi_output_path, "ndwi_file": ndwi_output_path,
    }


def save_log(rows):
    os.makedirs(os.path.dirname(log_csv_path), exist_ok=True)
    with open(log_csv_path, "w", newline="", encoding="utf-8") as csv_file:
        fieldnames = ["year_month", "ndvi_min", "ndvi_max", "ndwi_min", "ndwi_max", "decision", "ndvi_file", "ndwi_file"]
        csv_writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        csv_writer.writeheader()
        for row in rows:
            csv_writer.writerow(row)
    print("")
    print("Saved full NDVI/NDWI derivation log to " + log_csv_path)


def main():
    print("=====================================================")
    print("DERIVING MONTHLY NDVI AND NDWI AT 10m FROM ALREADY-VALIDATED SENTINEL-2 BANDS")
    print("=====================================================")
    print("No new Earth Engine downloads needed -- this reuses the 45 already-fetched, already-validated monthly band rasters.")

    monthly_files = find_all_monthly_band_files()

    rows = []
    for month_label, band_file_path in monthly_files:
        row = compute_ndvi_and_ndwi_for_one_month(month_label, band_file_path)
        rows.append(row)

    kept_count = sum(1 for row in rows if row["decision"] == "KEEP")
    print("")
    print("=====================================================")
    print("DONE. " + str(kept_count) + " out of " + str(len(rows)) + " months produced valid NDVI and NDWI rasters.")
    print("=====================================================")

    save_log(rows)


if __name__ == "__main__":
    main()
