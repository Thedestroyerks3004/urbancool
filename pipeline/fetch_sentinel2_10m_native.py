import os
import sys
import rasterio
import numpy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import sentinel2_composite
from pipeline.common import tiled_download

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "sentinel2_10m")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "sentinel2_10m")

native_10m_band_names = ["B2", "B3", "B4", "B8"]
tiles_per_side_for_download = 4


def fetch_native_10m_composite():
    print("Fetching the genuinely native-10m Sentinel-2 bands (B2 Blue, B3 Green, B4 Red, B8 NIR) ...")
    print("Excluded from this fetch: B11/B12 SWIR, which are natively 20m -- see check_resolution_disqualifications.py for that decision.")

    composite_image, rectangle, qualifying_scene_count = sentinel2_composite.fetch_cloud_free_sentinel2_median_composite(
        native_10m_band_names, "Sentinel-2 native 10m bands"
    )
    return composite_image, qualifying_scene_count


def decide_keep_or_drop_on_scene_count(qualifying_scene_count):
    print("")
    print("Deciding whether there is enough cloud-free Sentinel-2 coverage to build a trustworthy 10m composite ...")

    scene_count_check_passed = qualifying_scene_count >= thresholds.minimum_total_qualifying_scenes_across_solid_window
    print("Result: " + str(qualifying_scene_count) + " qualifying scenes >= required " + str(thresholds.minimum_total_qualifying_scenes_across_solid_window) + " -> " + ("PASS" if scene_count_check_passed else "FAIL"))

    return scene_count_check_passed


def validate_downloaded_raster(file_path):
    print("")
    print("Opening the downloaded native-10m raster with rasterio to confirm it is real and usable ...")

    if file_path is None or not os.path.exists(file_path):
        print("Validation failed: no file was downloaded.")
        return False, None

    with rasterio.open(file_path) as raster_dataset:
        raster_width_in_pixels = raster_dataset.width
        raster_height_in_pixels = raster_dataset.height
        pixel_width_meters, pixel_height_meters = raster_dataset.res
        coordinate_reference_system = raster_dataset.crs
        band_values = raster_dataset.read()

    print("Raster size: " + str(raster_width_in_pixels) + " x " + str(raster_height_in_pixels) + " pixels, reported pixel size " + str(pixel_width_meters) + " x " + str(pixel_height_meters) + " (CRS units: " + str(coordinate_reference_system) + ")")

    valid_pixel_mask = ~numpy.isnan(band_values).any(axis=0)
    valid_pixel_percentage = (valid_pixel_mask.sum() / valid_pixel_mask.size) * 100.0
    print("Pixels with valid values in all four bands: " + str(round(valid_pixel_percentage, 1)) + "%")

    red_band = band_values[2]
    nir_band = band_values[3]
    with numpy.errstate(invalid="ignore", divide="ignore"):
        ndvi_values = (nir_band - red_band) / (nir_band + red_band)

    valid_ndvi_values = ndvi_values[~numpy.isnan(ndvi_values)]
    minimum_ndvi = float(valid_ndvi_values.min()) if len(valid_ndvi_values) > 0 else None
    maximum_ndvi = float(valid_ndvi_values.max()) if len(valid_ndvi_values) > 0 else None
    print("NDVI range computed from the downloaded native-10m bands: " + str(round(minimum_ndvi, 3) if minimum_ndvi is not None else None) + " to " + str(round(maximum_ndvi, 3) if maximum_ndvi is not None else None))

    ndvi_in_valid_range = minimum_ndvi is not None and minimum_ndvi >= -1.0001 and maximum_ndvi <= 1.0001

    file_is_valid = valid_pixel_percentage >= thresholds.required_valid_pixel_percentage_for_10m_raster and ndvi_in_valid_range
    print("Result: " + str(round(valid_pixel_percentage, 1)) + "% valid pixels >= required " + str(thresholds.required_valid_pixel_percentage_for_10m_raster) + "%, and NDVI values physically valid: " + str(ndvi_in_valid_range) + " -> " + ("PASS" if file_is_valid else "FAIL"))

    return file_is_valid, valid_pixel_percentage


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: these native-10m bands are the direct input to a true 10m NDVI layer and are the covariate needed for any legitimate thermal sharpening (TsHARP/DisTrad) of coarser LST sources. Without them, no 10m-resolution vegetation or LST product can be built at all.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Sentinel-2 native 10m optical bands (B2, B3, B4, B8)")
    print("=====================================================")

    composite_image, qualifying_scene_count = fetch_native_10m_composite()

    if composite_image is None:
        decide_keep_or_drop_on_scene_count(0)
        return

    scene_count_check_passed = decide_keep_or_drop_on_scene_count(qualifying_scene_count)

    if not scene_count_check_passed:
        print("")
        print("FINAL DECISION: DROP. Not enough cloud-free Sentinel-2 scenes to build a trustworthy native-10m composite.")
        return

    minimum_longitude = study_area.study_area_minimum_longitude
    minimum_latitude = study_area.study_area_minimum_latitude
    maximum_longitude = study_area.study_area_maximum_longitude
    maximum_latitude = study_area.study_area_maximum_latitude

    downloaded_file_path = tiled_download.download_ee_image_in_tiles(
        composite_image,
        minimum_longitude,
        minimum_latitude,
        maximum_longitude,
        maximum_latitude,
        scale_in_meters=10,
        tiles_per_side=tiles_per_side_for_download,
        output_folder=output_folder_for_raw_data,
        base_filename="sentinel2_10m_bands",
    )

    file_is_valid, valid_pixel_percentage = validate_downloaded_raster(downloaded_file_path)

    final_decision_is_keep = file_is_valid

    print("")
    print("FINAL DECISION: " + ("KEEP" if final_decision_is_keep else "DROP") + " the native-10m Sentinel-2 bands dataset.")

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        validated_file_path = os.path.join(output_folder_for_validated_data, "sentinel2_10m_bands.tif")
        with open(downloaded_file_path, "rb") as source_file:
            file_contents = source_file.read()
        with open(validated_file_path, "wb") as destination_file:
            destination_file.write(file_contents)
        print("Copied validated raster to " + validated_file_path)


if __name__ == "__main__":
    main()
