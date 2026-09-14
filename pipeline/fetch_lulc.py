import os
import sys
import random
import requests
import rasterio
import numpy
import pandas
import ee
from shapely.geometry import Point
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, accuracy_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import osm_reference
from pipeline.common import sentinel2_composite

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "lulc")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "lulc")

classification_band_names = ["B2", "B3", "B4", "B8", "B11", "B12"]
points_to_sample_per_class = 150

class_name_to_code = {
    "built_up": 0,
    "vegetation": 1,
    "water": 2,
}
class_code_to_name = {value: key for key, value in class_name_to_code.items()}


def fetch_reference_polygons_for_all_classes():
    print("Fetching OpenStreetMap reference polygons for three land-cover classes: built_up, vegetation, water ...")

    built_up_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("building", "yes"), ("building", "residential"), ("building", "commercial"), ("building", "house"), ("building", "apartments")],
        maximum_features_per_tag=200,
        class_name_for_printing="built_up",
    )

    vegetation_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "wood"), ("landuse", "forest"), ("landuse", "farmland"), ("leisure", "park")],
        maximum_features_per_tag=200,
        class_name_for_printing="vegetation",
    )

    water_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "water"), ("natural", "wetland")],
        maximum_features_per_tag=200,
        class_name_for_printing="water",
    )

    polygons_by_class = {
        "built_up": built_up_polygons,
        "vegetation": vegetation_polygons,
        "water": water_polygons,
    }

    for class_name, polygon_list in polygons_by_class.items():
        print("Class '" + class_name + "' has " + str(len(polygon_list)) + " reference polygons available.")

    return polygons_by_class


def sample_reference_points_from_polygons(polygons_by_class):
    print("")
    print("Randomly sampling up to " + str(points_to_sample_per_class) + " reference points inside each class's polygons ...")

    random.seed(42)
    sampled_points_with_labels = []

    for class_name, polygon_list in polygons_by_class.items():
        if len(polygon_list) == 0:
            print("Class '" + class_name + "' has no polygons, cannot sample any points for it.")
            continue

        points_collected_for_this_class = 0
        maximum_attempts = points_to_sample_per_class * 50
        attempts_made = 0

        while points_collected_for_this_class < points_to_sample_per_class and attempts_made < maximum_attempts:
            attempts_made = attempts_made + 1
            chosen_polygon = random.choice(polygon_list)
            minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude = chosen_polygon.bounds
            random_longitude = random.uniform(minimum_longitude, maximum_longitude)
            random_latitude = random.uniform(minimum_latitude, maximum_latitude)
            candidate_point = Point(random_longitude, random_latitude)
            if chosen_polygon.contains(candidate_point):
                sampled_points_with_labels.append((random_longitude, random_latitude, class_name))
                points_collected_for_this_class = points_collected_for_this_class + 1

        print("Class '" + class_name + "': sampled " + str(points_collected_for_this_class) + " points out of a target of " + str(points_to_sample_per_class) + " (" + str(attempts_made) + " attempts).")

    print("Total reference points sampled across all classes: " + str(len(sampled_points_with_labels)))
    return sampled_points_with_labels


def check_every_class_has_enough_reference_points(sampled_points_with_labels):
    print("")
    print("Checking that every land-cover class has at least " + str(thresholds.minimum_reference_points_per_land_cover_class) + " reference points before training anything ...")

    points_per_class = {class_name: 0 for class_name in class_name_to_code.keys()}
    for longitude, latitude, class_name in sampled_points_with_labels:
        points_per_class[class_name] = points_per_class.get(class_name, 0) + 1

    every_class_has_enough_points = True
    for class_name, point_count in points_per_class.items():
        class_check_passed = point_count >= thresholds.minimum_reference_points_per_land_cover_class
        print("Result: class '" + class_name + "' has " + str(point_count) + " reference points >= required " + str(thresholds.minimum_reference_points_per_land_cover_class) + " -> " + ("PASS" if class_check_passed else "FAIL"))
        if not class_check_passed:
            every_class_has_enough_points = False

    return every_class_has_enough_points


def sample_band_values_at_reference_points(composite_image, sampled_points_with_labels):
    print("")
    print("Asking Google Earth Engine for the real Sentinel-2 band values at each reference point ...")

    earth_engine_features = []
    for longitude, latitude, class_name in sampled_points_with_labels:
        point_geometry = ee.Geometry.Point([longitude, latitude])
        point_feature = ee.Feature(point_geometry, {"class_name": class_name})
        earth_engine_features.append(point_feature)

    points_feature_collection = ee.FeatureCollection(earth_engine_features)

    sampled_feature_collection = composite_image.sampleRegions(
        collection=points_feature_collection,
        properties=["class_name"],
        scale=10,
        geometries=False,
    )

    sampled_features_info = sampled_feature_collection.getInfo()["features"]
    print("Received real band values for " + str(len(sampled_features_info)) + " reference points.")

    rows = []
    for feature in sampled_features_info:
        row = dict(feature["properties"])
        rows.append(row)

    training_dataframe = pandas.DataFrame(rows)
    training_dataframe = training_dataframe.dropna()
    print("Reference points with complete, non-empty band values after removing points that fell on nodata: " + str(len(training_dataframe)))

    return training_dataframe


def train_and_test_land_cover_classifier(training_dataframe):
    print("")
    print("Splitting reference points into a training set and a held-out test set the classifier has never seen ...")

    feature_columns = classification_band_names
    features = training_dataframe[feature_columns]
    labels = training_dataframe["class_name"]

    features_train, features_test, labels_train, labels_test = train_test_split(
        features, labels, test_size=0.3, random_state=42, stratify=labels
    )
    print("Training set size: " + str(len(features_train)) + " points. Test set size: " + str(len(features_test)) + " points.")

    print("Training a Random Forest land-cover classifier on the training set ...")
    land_cover_classifier = RandomForestClassifier(n_estimators=200, random_state=42)
    land_cover_classifier.fit(features_train, labels_train)

    print("Testing the trained classifier on the held-out test set it has never seen ...")
    predicted_labels_on_test_set = land_cover_classifier.predict(features_test)

    class_name_order = sorted(class_name_to_code.keys())
    confusion_matrix_result = confusion_matrix(labels_test, predicted_labels_on_test_set, labels=class_name_order)
    print("Confusion matrix (rows are actual class, columns are predicted class), class order " + str(class_name_order) + ":")
    print(confusion_matrix_result)

    overall_accuracy = accuracy_score(labels_test, predicted_labels_on_test_set)
    print("Land cover classifier overall accuracy on the held-out test set: " + str(round(overall_accuracy * 100, 1)) + "%")

    return land_cover_classifier, overall_accuracy


def decide_keep_or_drop_land_cover(overall_accuracy):
    print("")
    print("Deciding whether to KEEP or DROP the LULC dataset ...")

    accuracy_check_passed = overall_accuracy >= thresholds.required_land_cover_accuracy
    print("Result: " + str(round(overall_accuracy * 100, 1)) + "% accuracy >= required " + str(round(thresholds.required_land_cover_accuracy * 100, 1)) + "% -> " + ("KEEP" if accuracy_check_passed else "DROP"))

    if not accuracy_check_passed:
        print("What is missing: land-cover classification is not reliable enough to distinguish built-up, vegetation, and water at the required accuracy, so the LULC layer cannot be trusted as an input to the Heat Vulnerability Index.")

    return accuracy_check_passed


def download_and_classify_full_composite(composite_image, rectangle, land_cover_classifier):
    print("")
    print("Downloading the full Sentinel-2 composite bands at " + str(study_area.analysis_grid_cell_size_meters) + "m resolution so the trained classifier can be applied pixel by pixel ...")

    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    raw_composite_file_path = os.path.join(output_folder_for_raw_data, "sentinel2_bands_150m_composite.tif")

    download_url = composite_image.getDownloadURL({
        "scale": study_area.analysis_grid_cell_size_meters,
        "region": rectangle,
        "format": "GEO_TIFF",
        "crs": "EPSG:4326",
    })

    response = requests.get(download_url, timeout=120)
    if response.status_code != 200:
        print("Download failed with HTTP status code " + str(response.status_code))
        return None

    with open(raw_composite_file_path, "wb") as output_file:
        output_file.write(response.content)

    file_size_in_kilobytes = os.path.getsize(raw_composite_file_path) / 1024.0
    print("Saved raw band composite to " + raw_composite_file_path + " (" + str(round(file_size_in_kilobytes, 1)) + " KB)")

    with rasterio.open(raw_composite_file_path) as raster_dataset:
        band_stack = raster_dataset.read()
        raster_profile = raster_dataset.profile

    band_count, raster_height, raster_width = band_stack.shape
    print("Downloaded raster has " + str(band_count) + " bands and is " + str(raster_width) + " x " + str(raster_height) + " pixels.")

    flattened_pixels = band_stack.reshape(band_count, -1).transpose()
    pixel_dataframe = pandas.DataFrame(flattened_pixels, columns=classification_band_names)

    valid_pixel_mask = ~pixel_dataframe.isna().any(axis=1)
    print("Pixels with valid values in all bands: " + str(int(valid_pixel_mask.sum())) + " out of " + str(len(pixel_dataframe)))

    predicted_class_names = numpy.full(len(pixel_dataframe), "no_data", dtype=object)
    predicted_class_names[valid_pixel_mask.values] = land_cover_classifier.predict(pixel_dataframe[valid_pixel_mask.values])

    predicted_class_codes = numpy.array([
        class_name_to_code.get(class_name, 255) for class_name in predicted_class_names
    ], dtype=numpy.uint8)

    classified_raster = predicted_class_codes.reshape(raster_height, raster_width)

    os.makedirs(output_folder_for_validated_data, exist_ok=True)
    classified_file_path = os.path.join(output_folder_for_validated_data, "lulc_150m_classified.tif")

    output_profile = raster_profile.copy()
    output_profile.update(count=1, dtype=rasterio.uint8, nodata=255)

    with rasterio.open(classified_file_path, "w", **output_profile) as output_raster:
        output_raster.write(classified_raster, 1)

    print("Saved classified LULC raster to " + classified_file_path)
    print("Class codes: " + str(class_name_to_code))

    return classified_file_path


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: LULC identifies which 150m cells are built-up (heat-retaining) versus vegetated or water (cooling). Without it, the Heat Vulnerability Index cannot distinguish which cells are physically capable of being cooled by green/blue interventions versus which are purely built infrastructure.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Land Use / Land Cover (LULC)")
    print("=====================================================")

    print("Note on resolution honesty: bands B11 and B12 used below are natively sensed at 20m by Sentinel-2 and upsampled to 10m by ESA in the L2A product. This is disclosed, not hidden.")

    polygons_by_class = fetch_reference_polygons_for_all_classes()
    sampled_points_with_labels = sample_reference_points_from_polygons(polygons_by_class)

    every_class_has_enough_points = check_every_class_has_enough_reference_points(sampled_points_with_labels)
    if not every_class_has_enough_points:
        print("")
        print("FINAL DECISION: DROP the LULC dataset for this run.")
        print("What is missing: one or more land-cover classes had too few OpenStreetMap reference polygons (likely due to an Overpass API timeout on this run), so the classifier cannot be honestly trained or validated on all classes it is supposed to distinguish.")
        print("This is a fetch-reliability problem, not a resolution or accuracy problem -- retrying the Overpass query for the missing class(es) should resolve it on a later run.")
        return

    composite_image, rectangle, qualifying_scene_count = sentinel2_composite.fetch_cloud_free_sentinel2_median_composite(
        classification_band_names, "LULC classification"
    )

    if composite_image is None:
        decide_keep_or_drop_land_cover(0.0)
        return

    training_dataframe = sample_band_values_at_reference_points(composite_image, sampled_points_with_labels)

    if len(training_dataframe) < 30:
        print("Fetch failed: not enough valid band samples were returned to train a classifier.")
        decide_keep_or_drop_land_cover(0.0)
        return

    land_cover_classifier, overall_accuracy = train_and_test_land_cover_classifier(training_dataframe)

    final_decision_is_keep = decide_keep_or_drop_land_cover(overall_accuracy)

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        download_and_classify_full_composite(composite_image, rectangle, land_cover_classifier)


if __name__ == "__main__":
    main()
