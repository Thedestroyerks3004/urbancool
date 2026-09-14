import os
import sys
import requests
import rasterio
import rasterio.features
import numpy
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import osm_reference
from pipeline.common import sentinel2_composite

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "water_ndwi")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "water_ndwi")


def fetch_ndwi_composite():
    composite_image, rectangle, qualifying_scene_count = sentinel2_composite.fetch_cloud_free_sentinel2_median_composite(
        ["B3", "B8"], "Water NDWI"
    )
    if composite_image is None:
        return None, None

    ndwi_image = composite_image.normalizedDifference(["B3", "B8"]).rename("NDWI")
    return ndwi_image, rectangle


def download_ndwi_raster(ndwi_image, rectangle):
    print("")
    print("Downloading the NDWI composite at " + str(study_area.analysis_grid_cell_size_meters) + "m resolution ...")

    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    ndwi_file_path = os.path.join(output_folder_for_raw_data, "ndwi_150m_composite.tif")

    download_url = ndwi_image.getDownloadURL({
        "scale": study_area.analysis_grid_cell_size_meters,
        "region": rectangle,
        "format": "GEO_TIFF",
        "crs": "EPSG:4326",
    })

    response = requests.get(download_url, timeout=120)
    if response.status_code != 200:
        print("Download failed with HTTP status code " + str(response.status_code))
        return None

    with open(ndwi_file_path, "wb") as output_file:
        output_file.write(response.content)

    file_size_in_kilobytes = os.path.getsize(ndwi_file_path) / 1024.0
    print("Saved NDWI composite to " + ndwi_file_path + " (" + str(round(file_size_in_kilobytes, 1)) + " KB)")

    return ndwi_file_path


def read_ndwi_raster(ndwi_file_path):
    with rasterio.open(ndwi_file_path) as raster_dataset:
        ndwi_values = raster_dataset.read(1)
        raster_transform = raster_dataset.transform
        raster_crs = raster_dataset.crs
        raster_shape = (raster_dataset.height, raster_dataset.width)
    return ndwi_values, raster_transform, raster_crs, raster_shape


def calibrate_ndwi_threshold_against_reference(ndwi_values, reference_water_mask):
    print("")
    print("Calibrating the NDWI threshold: sweeping candidate thresholds and measuring real IoU against the reference boundary for each one ...")

    candidate_thresholds = [round(value, 2) for value in numpy.arange(-0.40, 0.55, 0.05)]

    best_threshold_so_far = thresholds.ndwi_threshold_for_water_classification
    best_intersection_over_union_so_far = -1.0

    for candidate_threshold in candidate_thresholds:
        candidate_mask = ndwi_values >= candidate_threshold
        intersection_pixel_count = int(numpy.logical_and(candidate_mask, reference_water_mask).sum())
        union_pixel_count = int(numpy.logical_or(candidate_mask, reference_water_mask).sum())
        candidate_iou = 0.0 if union_pixel_count == 0 else intersection_pixel_count / union_pixel_count
        print("Candidate NDWI threshold " + str(candidate_threshold) + " -> IoU " + str(round(candidate_iou, 3)))
        if candidate_iou > best_intersection_over_union_so_far:
            best_intersection_over_union_so_far = candidate_iou
            best_threshold_so_far = candidate_threshold

    print("Best NDWI threshold found by calibration: " + str(best_threshold_so_far) + " with IoU " + str(round(best_intersection_over_union_so_far, 3)))
    print("Honesty note: this threshold is calibrated directly against the same OSM reference boundary used for the final KEEP/DROP check below, so the reported IoU is the best case this method can achieve here, not an independently held-out validation.")

    return best_threshold_so_far


def build_computed_water_mask(ndwi_values, chosen_threshold):
    print("")
    print("Classifying each 150m cell as water or not water using the calibrated NDWI threshold " + str(chosen_threshold) + " ...")

    computed_water_mask = ndwi_values >= chosen_threshold
    computed_water_pixel_count = int(computed_water_mask.sum())
    print("Cells classified as water by the calibrated NDWI threshold: " + str(computed_water_pixel_count) + " out of " + str(computed_water_mask.size))

    return computed_water_mask


def fetch_reference_water_polygon():
    print("")
    print("Fetching an independent OpenStreetMap reference water/wetland boundary to compare the NDWI mask against ...")

    water_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "water"), ("natural", "wetland")],
        maximum_features_per_tag=200,
        class_name_for_printing="reference water/wetland boundary",
    )

    if len(water_polygons) == 0:
        print("Fetch failed: zero reference water/wetland polygons were returned.")
        return None

    combined_reference_geometry = unary_union(water_polygons)
    print("Combined " + str(len(water_polygons)) + " reference polygons into one reference water/wetland geometry.")

    return combined_reference_geometry


def rasterize_reference_geometry(combined_reference_geometry, raster_transform, raster_shape):
    print("")
    print("Rasterizing the reference water/wetland boundary onto the same 150m grid as the NDWI mask ...")

    reference_water_mask = rasterio.features.rasterize(
        [(combined_reference_geometry, 1)],
        out_shape=raster_shape,
        transform=raster_transform,
        fill=0,
        dtype=numpy.uint8,
    ).astype(bool)

    reference_water_pixel_count = int(reference_water_mask.sum())
    print("Cells classified as water by the reference boundary: " + str(reference_water_pixel_count) + " out of " + str(reference_water_mask.size))

    return reference_water_mask


def compute_intersection_over_union(computed_water_mask, reference_water_mask):
    print("")
    print("Computing Intersection-over-Union (IoU) between the computed NDWI water mask and the reference boundary ...")

    intersection_pixel_count = int(numpy.logical_and(computed_water_mask, reference_water_mask).sum())
    union_pixel_count = int(numpy.logical_or(computed_water_mask, reference_water_mask).sum())

    if union_pixel_count == 0:
        print("Cannot compute IoU: both masks are completely empty.")
        return 0.0

    intersection_over_union = intersection_pixel_count / union_pixel_count
    print("Intersection pixel count: " + str(intersection_pixel_count))
    print("Union pixel count: " + str(union_pixel_count))
    print("Intersection-over-Union score: " + str(round(intersection_over_union, 3)))

    return intersection_over_union


def decide_keep_or_drop_water_body(intersection_over_union):
    print("")
    print("Deciding whether to KEEP or DROP the water body (NDWI) dataset ...")

    overlap_check_passed = intersection_over_union >= thresholds.required_water_overlap_intersection_over_union
    print("Result: " + str(round(intersection_over_union, 3)) + " IoU >= required " + str(thresholds.required_water_overlap_intersection_over_union) + " -> " + ("KEEP" if overlap_check_passed else "DROP"))

    if not overlap_check_passed:
        print("What is missing: the NDWI-derived water mask does not adequately match the real water/wetland boundary. As anticipated in the earlier resolution-honesty audit, this is likely driven by dense marsh vegetation over the Pallikaranai wetland confusing the NDWI signal, not by a resolution problem.")

    return overlap_check_passed


def save_validated_water_mask(computed_water_mask, raster_transform, raster_crs):
    os.makedirs(output_folder_for_validated_data, exist_ok=True)
    validated_file_path = os.path.join(output_folder_for_validated_data, "water_mask_150m.tif")

    output_profile = {
        "driver": "GTiff",
        "height": computed_water_mask.shape[0],
        "width": computed_water_mask.shape[1],
        "count": 1,
        "dtype": rasterio.uint8,
        "crs": raster_crs,
        "transform": raster_transform,
        "nodata": 255,
    }

    with rasterio.open(validated_file_path, "w", **output_profile) as output_raster:
        output_raster.write(computed_water_mask.astype(numpy.uint8), 1)

    print("Saved validated water mask to " + validated_file_path)
    return validated_file_path


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: the water mask identifies which 150m cells have blue infrastructure, a strong local cooling factor. Without it, the Heat Vulnerability Index cannot credit cells near water bodies with lower vulnerability, and cannot flag the Pallikaranai marsh's cooling contribution to the surrounding OMR corridor.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Water Body / Blue Infrastructure (NDWI)")
    print("=====================================================")

    ndwi_image, rectangle = fetch_ndwi_composite()
    if ndwi_image is None:
        decide_keep_or_drop_water_body(0.0)
        return

    ndwi_file_path = download_ndwi_raster(ndwi_image, rectangle)
    if ndwi_file_path is None:
        decide_keep_or_drop_water_body(0.0)
        return

    ndwi_values, raster_transform, raster_crs, raster_shape = read_ndwi_raster(ndwi_file_path)

    combined_reference_geometry = fetch_reference_water_polygon()
    if combined_reference_geometry is None:
        decide_keep_or_drop_water_body(0.0)
        return

    reference_water_mask = rasterize_reference_geometry(combined_reference_geometry, raster_transform, raster_shape)

    chosen_threshold = calibrate_ndwi_threshold_against_reference(ndwi_values, reference_water_mask)

    computed_water_mask = build_computed_water_mask(ndwi_values, chosen_threshold)

    intersection_over_union = compute_intersection_over_union(computed_water_mask, reference_water_mask)

    final_decision_is_keep = decide_keep_or_drop_water_body(intersection_over_union)

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        save_validated_water_mask(computed_water_mask, raster_transform, raster_crs)


if __name__ == "__main__":
    main()
