import os
import sys
import random
import rasterio
import ee
from shapely.geometry import Point

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import osm_reference
from pipeline.common import tiled_download

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "worldcover")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "worldcover")

points_to_sample_per_class = 100

worldcover_code_to_our_class = {
    10: "vegetation",
    20: "vegetation",
    30: "vegetation",
    40: "vegetation",
    50: "built_up",
    60: "other",
    70: "other",
    80: "water",
    90: "water",
    95: "water",
    100: "other",
}


def fetch_worldcover_image():
    print("Fetching ESA WorldCover v200 (2021) land cover map ...")
    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    worldcover_image = ee.Image(ee.ImageCollection("ESA/WorldCover/v200").first())
    worldcover_nominal_scale_meters = worldcover_image.select("Map").projection().nominalScale().getInfo()
    print("Earth Engine reports the native projection scale of ESA WorldCover as: " + str(worldcover_nominal_scale_meters) + " meters")

    clipped_worldcover_image = worldcover_image.select("Map").clip(rectangle)
    return clipped_worldcover_image, worldcover_nominal_scale_meters


def download_worldcover_raster(worldcover_image):
    print("")
    downloaded_file_path = tiled_download.download_ee_image_in_tiles(
        worldcover_image,
        study_area.study_area_minimum_longitude,
        study_area.study_area_minimum_latitude,
        study_area.study_area_maximum_longitude,
        study_area.study_area_maximum_latitude,
        scale_in_meters=10,
        tiles_per_side=3,
        output_folder=output_folder_for_raw_data,
        base_filename="worldcover_10m",
    )
    return downloaded_file_path


def sample_reference_points_for_validation():
    print("")
    print("Fetching independent OpenStreetMap reference points to validate WorldCover, separately from any points used for the LULC classifier ...")

    built_up_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("building", "yes"), ("building", "residential"), ("building", "commercial")],
        maximum_features_per_tag=200,
        class_name_for_printing="built_up (validation)",
    )
    vegetation_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "wood"), ("landuse", "forest"), ("landuse", "farmland")],
        maximum_features_per_tag=200,
        class_name_for_printing="vegetation (validation)",
    )
    water_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "water"), ("natural", "wetland")],
        maximum_features_per_tag=200,
        class_name_for_printing="water (validation)",
    )

    polygons_by_class = {
        "built_up": built_up_polygons,
        "vegetation": vegetation_polygons,
        "water": water_polygons,
    }

    random.seed(7)
    sampled_points_with_labels = []
    for class_name, polygon_list in polygons_by_class.items():
        if len(polygon_list) == 0:
            print("Class '" + class_name + "' has zero reference polygons, skipping.")
            continue

        points_collected = 0
        attempts_made = 0
        maximum_attempts = points_to_sample_per_class * 50
        while points_collected < points_to_sample_per_class and attempts_made < maximum_attempts:
            attempts_made = attempts_made + 1
            chosen_polygon = random.choice(polygon_list)
            minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude = chosen_polygon.bounds
            random_longitude = random.uniform(minimum_longitude, maximum_longitude)
            random_latitude = random.uniform(minimum_latitude, maximum_latitude)
            candidate_point = Point(random_longitude, random_latitude)
            if chosen_polygon.contains(candidate_point):
                sampled_points_with_labels.append((random_longitude, random_latitude, class_name))
                points_collected = points_collected + 1

        print("Class '" + class_name + "': sampled " + str(points_collected) + " validation points.")

    return sampled_points_with_labels


def compare_worldcover_to_reference_points(worldcover_file_path, sampled_points_with_labels):
    print("")
    print("Sampling the downloaded WorldCover raster at each independent reference point and checking agreement ...")

    with rasterio.open(worldcover_file_path) as raster_dataset:
        coordinates = [(longitude, latitude) for longitude, latitude, class_name in sampled_points_with_labels]
        sampled_worldcover_codes = list(raster_dataset.sample(coordinates))

    agreement_count = 0
    total_checked = 0
    for (longitude, latitude, expected_class_name), sampled_value_array in zip(sampled_points_with_labels, sampled_worldcover_codes):
        worldcover_code = int(sampled_value_array[0])
        predicted_class_name = worldcover_code_to_our_class.get(worldcover_code, "other")
        total_checked = total_checked + 1
        if predicted_class_name == expected_class_name:
            agreement_count = agreement_count + 1

    if total_checked == 0:
        print("Cannot compute agreement: zero reference points were available.")
        return 0.0

    agreement_fraction = agreement_count / total_checked
    print("Agreement between WorldCover and independent OSM reference points: " + str(agreement_count) + " out of " + str(total_checked) + " (" + str(round(agreement_fraction * 100, 1)) + "%)")

    return agreement_fraction


def decide_keep_or_drop_worldcover(worldcover_nominal_scale_meters, agreement_fraction):
    print("")
    print("Deciding whether to KEEP or DROP the WorldCover dataset ...")

    resolution_check_passed = worldcover_nominal_scale_meters <= 10.0
    print("Result: " + str(worldcover_nominal_scale_meters) + "m native resolution <= required 10m -> " + ("PASS" if resolution_check_passed else "FAIL"))

    agreement_check_passed = agreement_fraction >= thresholds.required_worldcover_agreement_with_osm_reference
    print("Result: " + str(round(agreement_fraction * 100, 1)) + "% agreement with independent reference points >= required " + str(round(thresholds.required_worldcover_agreement_with_osm_reference * 100, 1)) + "% -> " + ("PASS" if agreement_check_passed else "FAIL"))

    final_decision_is_keep = resolution_check_passed and agreement_check_passed

    if final_decision_is_keep:
        print("FINAL DECISION: KEEP the WorldCover dataset.")
    else:
        print("FINAL DECISION: DROP the WorldCover dataset.")
        if not agreement_check_passed:
            print("What is missing: WorldCover's classification does not agree well enough with independently sourced OSM reference points in this specific corridor, so it cannot be trusted as-is for local land-cover attribution here.")

    return final_decision_is_keep


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: WorldCover provides a genuinely native 10m, globally validated land-cover baseline, independent of the project's own Sentinel-2 classifier. Without it, there is no independent cross-check on the self-trained LULC classifier, and no ready-made global product to fall back on if that classifier's accuracy degrades in future runs.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: ESA WorldCover v200 (10m)")
    print("=====================================================")

    worldcover_image, worldcover_nominal_scale_meters = fetch_worldcover_image()

    downloaded_file_path = download_worldcover_raster(worldcover_image)
    if downloaded_file_path is None:
        decide_keep_or_drop_worldcover(worldcover_nominal_scale_meters, 0.0)
        return

    sampled_points_with_labels = sample_reference_points_for_validation()
    if len(sampled_points_with_labels) < 30:
        print("Validation failed: not enough independent reference points were sampled.")
        decide_keep_or_drop_worldcover(worldcover_nominal_scale_meters, 0.0)
        return

    agreement_fraction = compare_worldcover_to_reference_points(downloaded_file_path, sampled_points_with_labels)

    final_decision_is_keep = decide_keep_or_drop_worldcover(worldcover_nominal_scale_meters, agreement_fraction)

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        validated_file_path = os.path.join(output_folder_for_validated_data, "worldcover_10m.tif")
        with open(downloaded_file_path, "rb") as source_file:
            file_contents = source_file.read()
        with open(validated_file_path, "wb") as destination_file:
            destination_file.write(file_contents)
        print("Copied validated raster to " + validated_file_path)


if __name__ == "__main__":
    main()
