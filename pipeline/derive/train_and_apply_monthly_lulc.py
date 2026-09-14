import os
import sys
import random
import rasterio
import numpy
import pandas
import ee
from shapely.geometry import Point
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, accuracy_score

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.common import study_area
from pipeline.common import thresholds
from pipeline.common import osm_reference
from pipeline.common import sentinel2_composite

source_monthly_bands_folder = os.path.join("D:\\", "Projects", "UC", "data", "validated", "sentinel2_10m_monthly_least_cloudy")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "lulc_10m_monthly")

classification_band_names = ["B2", "B3", "B4", "B8"]
points_to_sample_per_class = 150

class_name_to_code = {
    "built_up": 0,
    "vegetation": 1,
    "water": 2,
}


def fetch_reference_polygons_for_all_classes():
    print("Fetching OpenStreetMap reference polygons for the narrowed Anna University / OMR-ECR study area ...")

    built_up_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("building", "yes"), ("building", "residential"), ("building", "commercial"), ("building", "house"), ("building", "apartments")],
        maximum_features_per_tag=200, class_name_for_printing="built_up",
    )
    vegetation_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "wood"), ("landuse", "forest"), ("landuse", "farmland"), ("leisure", "park")],
        maximum_features_per_tag=200, class_name_for_printing="vegetation",
    )
    water_polygons = osm_reference.fetch_osm_polygons_for_tag_list(
        [("natural", "water"), ("natural", "wetland")],
        maximum_features_per_tag=200, class_name_for_printing="water",
    )

    polygons_by_class = {"built_up": built_up_polygons, "vegetation": vegetation_polygons, "water": water_polygons}
    for class_name, polygon_list in polygons_by_class.items():
        print("Class '" + class_name + "' has " + str(len(polygon_list)) + " reference polygons available.")
    return polygons_by_class


def sample_reference_points_from_polygons(polygons_by_class):
    print("")
    print("Randomly sampling up to " + str(points_to_sample_per_class) + " reference points per class ...")
    random.seed(42)
    sampled_points_with_labels = []

    for class_name, polygon_list in polygons_by_class.items():
        if len(polygon_list) == 0:
            print("Class '" + class_name + "' has no polygons, skipping.")
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
        print("Class '" + class_name + "': sampled " + str(points_collected) + " points (" + str(attempts_made) + " attempts).")

    print("Total reference points sampled: " + str(len(sampled_points_with_labels)))
    return sampled_points_with_labels


def check_every_class_has_enough_points(sampled_points_with_labels):
    points_per_class = {class_name: 0 for class_name in class_name_to_code.keys()}
    for longitude, latitude, class_name in sampled_points_with_labels:
        points_per_class[class_name] = points_per_class.get(class_name, 0) + 1

    every_class_ok = True
    for class_name, count in points_per_class.items():
        class_ok = count >= thresholds.minimum_reference_points_per_land_cover_class
        print("Result: class '" + class_name + "' has " + str(count) + " points >= required " + str(thresholds.minimum_reference_points_per_land_cover_class) + " -> " + ("PASS" if class_ok else "FAIL"))
        if not class_ok:
            every_class_ok = False
    return every_class_ok


def sample_band_values_at_reference_points(composite_image, sampled_points_with_labels):
    print("")
    print("Sampling real Sentinel-2 band values (native 10m bands only, no SWIR) at each reference point ...")

    earth_engine_features = []
    for longitude, latitude, class_name in sampled_points_with_labels:
        point_geometry = ee.Geometry.Point([longitude, latitude])
        earth_engine_features.append(ee.Feature(point_geometry, {"class_name": class_name}))

    points_feature_collection = ee.FeatureCollection(earth_engine_features)
    sampled_feature_collection = composite_image.sampleRegions(
        collection=points_feature_collection, properties=["class_name"], scale=10, geometries=False,
    )

    sampled_features_info = sampled_feature_collection.getInfo()["features"]
    print("Received real band values for " + str(len(sampled_features_info)) + " reference points.")

    rows = [dict(feature["properties"]) for feature in sampled_features_info]
    training_dataframe = pandas.DataFrame(rows).dropna()
    print("Reference points with complete band values: " + str(len(training_dataframe)))
    return training_dataframe


def train_and_test_classifier(training_dataframe):
    print("")
    print("Splitting into train/test and training a Random Forest classifier on native-10m bands only (B2,B3,B4,B8) ...")

    features = training_dataframe[classification_band_names]
    labels = training_dataframe["class_name"]
    features_train, features_test, labels_train, labels_test = train_test_split(
        features, labels, test_size=0.3, random_state=42, stratify=labels
    )
    print("Training set: " + str(len(features_train)) + ", test set: " + str(len(features_test)))

    classifier = RandomForestClassifier(n_estimators=200, random_state=42)
    classifier.fit(features_train, labels_train)

    predicted_labels = classifier.predict(features_test)
    class_order = sorted(class_name_to_code.keys())
    confusion = confusion_matrix(labels_test, predicted_labels, labels=class_order)
    print("Confusion matrix, class order " + str(class_order) + ":")
    print(confusion)

    accuracy = accuracy_score(labels_test, predicted_labels)
    print("Overall accuracy on held-out test set: " + str(round(accuracy * 100, 1)) + "%")

    return classifier, accuracy


def decide_keep_or_drop_classifier(accuracy):
    print("")
    accuracy_check_passed = accuracy >= thresholds.required_land_cover_accuracy
    print("Result: " + str(round(accuracy * 100, 1)) + "% accuracy >= required " + str(round(thresholds.required_land_cover_accuracy * 100, 1)) + "% -> " + ("KEEP" if accuracy_check_passed else "DROP"))
    return accuracy_check_passed


def apply_classifier_to_all_months(classifier):
    print("")
    print("Applying the trained classifier to every already-fetched, already-validated monthly raster ...")

    month_folders = sorted(os.listdir(source_monthly_bands_folder))
    applied_count = 0

    for month_label in month_folders:
        month_folder_path = os.path.join(source_monthly_bands_folder, month_label)
        if not os.path.isdir(month_folder_path):
            continue
        band_file_path = os.path.join(month_folder_path, "sentinel2_10m_least_cloudy_" + month_label + ".tif")
        if not os.path.exists(band_file_path):
            continue

        output_file_path_check = os.path.join(output_folder_for_validated_data, month_label, "lulc_10m_" + month_label + ".tif")
        if os.path.exists(output_file_path_check):
            print(month_label + ": already classified, skipping (resumable run).")
            applied_count = applied_count + 1
            continue

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

        band_count, height, width = band_values.shape
        flattened_pixels = band_values.reshape(band_count, -1).transpose()
        pixel_dataframe = pandas.DataFrame(flattened_pixels, columns=classification_band_names)

        predicted_class_names = numpy.full(len(pixel_dataframe), "no_data", dtype=object)
        valid_flat_mask = valid_pixel_mask.reshape(-1)
        if valid_flat_mask.sum() > 0:
            predicted_class_names[valid_flat_mask] = classifier.predict(pixel_dataframe[valid_flat_mask])

        predicted_class_codes = numpy.array([
            class_name_to_code.get(name, 255) for name in predicted_class_names
        ], dtype=numpy.uint8)
        classified_raster = predicted_class_codes.reshape(height, width)

        os.makedirs(os.path.join(output_folder_for_validated_data, month_label), exist_ok=True)
        output_file_path = os.path.join(output_folder_for_validated_data, month_label, "lulc_10m_" + month_label + ".tif")

        output_profile = raster_profile.copy()
        output_profile.update(count=1, dtype=rasterio.uint8, nodata=255)
        with rasterio.open(output_file_path, "w", **output_profile) as output_raster:
            output_raster.write(classified_raster, 1)

        built_up_fraction = (classified_raster == 0).sum() / valid_flat_mask.sum() * 100.0 if valid_flat_mask.sum() > 0 else 0.0
        vegetation_fraction = (classified_raster == 1).sum() / valid_flat_mask.sum() * 100.0 if valid_flat_mask.sum() > 0 else 0.0
        water_fraction = (classified_raster == 2).sum() / valid_flat_mask.sum() * 100.0 if valid_flat_mask.sum() > 0 else 0.0

        print(month_label + ": built_up=" + str(round(built_up_fraction, 1)) + "%, vegetation=" + str(round(vegetation_fraction, 1)) + "%, water=" + str(round(water_fraction, 1)) + "% -> saved " + output_file_path)
        applied_count = applied_count + 1

    print("")
    print("Applied classifier to " + str(applied_count) + " months.")


def main():
    print("=====================================================")
    print("TRAINING A NATIVE-10m-ONLY LULC CLASSIFIER AND APPLYING IT TO ALL MONTHS")
    print("=====================================================")
    print("Note: the earlier LULC classifier used B11/B12 SWIR (20m native). This retrains on B2,B3,B4,B8 only, to stay consistent with this track's 10m-or-finer rule and to match the bands already downloaded for every month.")

    polygons_by_class = fetch_reference_polygons_for_all_classes()
    sampled_points_with_labels = sample_reference_points_from_polygons(polygons_by_class)

    every_class_ok = check_every_class_has_enough_points(sampled_points_with_labels)
    if not every_class_ok:
        print("")
        print("FINAL DECISION: DROP. One or more classes had too few reference points (likely an Overpass fetch issue this run).")
        return

    composite_image, rectangle, qualifying_scene_count = sentinel2_composite.fetch_cloud_free_sentinel2_median_composite(
        classification_band_names, "Monthly LULC training (native 10m bands only)"
    )
    if composite_image is None:
        print("")
        print("FINAL DECISION: DROP. Could not build a training composite.")
        return

    training_dataframe = sample_band_values_at_reference_points(composite_image, sampled_points_with_labels)
    if len(training_dataframe) < 30:
        print("")
        print("FINAL DECISION: DROP. Not enough valid training samples.")
        return

    classifier, accuracy = train_and_test_classifier(training_dataframe)
    final_decision_is_keep = decide_keep_or_drop_classifier(accuracy)

    if not final_decision_is_keep:
        print("")
        print("FINAL DECISION: DROP. Classifier accuracy too low to apply to monthly data.")
        return

    print("")
    print("FINAL DECISION: KEEP. Applying this classifier across all months now.")
    apply_classifier_to_all_months(classifier)


if __name__ == "__main__":
    main()
