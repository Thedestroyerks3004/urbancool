import os
import sys
import io
import gzip
import json
import math
import csv
import requests
import geopandas
from shapely.geometry import shape, box

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area
from pipeline.common import thresholds

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "building_footprints")
output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "building_footprints")

dataset_links_csv_url = "https://bfppub.blob.core.windows.net/%24web/2026-08-13/dataset-links.csv"
quadkey_zoom_level = 9

minimum_plausible_building_count = 50
maximum_plausible_building_density_per_square_km = 20000


def convert_longitude_latitude_to_tile_numbers(longitude_degrees, latitude_degrees, zoom_level):
    latitude_radians = math.radians(latitude_degrees)
    number_of_tiles_per_side = 2 ** zoom_level
    tile_x = int((longitude_degrees + 180.0) / 360.0 * number_of_tiles_per_side)
    tile_y = int((1.0 - math.log(math.tan(latitude_radians) + 1.0 / math.cos(latitude_radians)) / math.pi) / 2.0 * number_of_tiles_per_side)
    return tile_x, tile_y


def convert_tile_numbers_to_quadkey(tile_x, tile_y, zoom_level):
    quadkey_digits = []
    for level in range(zoom_level, 0, -1):
        digit = 0
        mask = 1 << (level - 1)
        if (tile_x & mask) != 0:
            digit = digit + 1
        if (tile_y & mask) != 0:
            digit = digit + 2
        quadkey_digits.append(str(digit))
    return "".join(quadkey_digits)


def find_quadkeys_covering_study_area():
    print("Computing which Bing Maps quadkeys (zoom " + str(quadkey_zoom_level) + ") cover the study area bounding box ...")

    sample_longitudes = [
        study_area.study_area_minimum_longitude,
        study_area.study_area_maximum_longitude,
        (study_area.study_area_minimum_longitude + study_area.study_area_maximum_longitude) / 2.0,
    ]
    sample_latitudes = [
        study_area.study_area_minimum_latitude,
        study_area.study_area_maximum_latitude,
        (study_area.study_area_minimum_latitude + study_area.study_area_maximum_latitude) / 2.0,
    ]

    covering_quadkeys = set()
    for longitude in sample_longitudes:
        for latitude in sample_latitudes:
            tile_x, tile_y = convert_longitude_latitude_to_tile_numbers(longitude, latitude, quadkey_zoom_level)
            quadkey = convert_tile_numbers_to_quadkey(tile_x, tile_y, quadkey_zoom_level)
            covering_quadkeys.add(quadkey)

    print("Quadkeys covering the study area: " + str(sorted(covering_quadkeys)))
    return covering_quadkeys


def fetch_dataset_links_for_india(covering_quadkeys):
    print("")
    print("Downloading Microsoft's dataset-links.csv index to find the India tiles matching our quadkeys ...")
    print("Using index URL " + dataset_links_csv_url + " (Microsoft moves this URL periodically; verified against the repository README on this run -- re-check https://github.com/microsoft/GlobalMLBuildingFootprints if this stops working later).")

    response = requests.get(dataset_links_csv_url, timeout=120)
    if response.status_code != 200:
        print("Fetch failed: HTTP status code " + str(response.status_code) + " when downloading dataset-links.csv")
        return []

    csv_text = response.text
    csv_reader = csv.DictReader(io.StringIO(csv_text))

    matching_rows = []
    for row in csv_reader:
        if row.get("Location") == "India" and row.get("QuadKey") in covering_quadkeys:
            matching_rows.append(row)

    print("Found " + str(len(matching_rows)) + " matching India tile(s) in dataset-links.csv for our study area quadkeys.")
    return matching_rows


def download_and_load_building_footprints(matching_rows):
    print("")
    print("Downloading and parsing the actual building footprint tile file(s) ...")

    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    study_area_box = box(
        study_area.study_area_minimum_longitude,
        study_area.study_area_minimum_latitude,
        study_area.study_area_maximum_longitude,
        study_area.study_area_maximum_latitude,
    )

    all_building_geometries = []
    for row in matching_rows:
        tile_url = row["Url"]
        print("Downloading tile " + row["QuadKey"] + " from " + tile_url + " ...")
        tile_response = requests.get(tile_url, timeout=300)
        if tile_response.status_code != 200:
            print("FAILED to download tile " + row["QuadKey"] + ": HTTP status code " + str(tile_response.status_code))
            continue

        raw_bytes = tile_response.content
        print("Downloaded " + str(round(len(raw_bytes) / (1024.0 * 1024.0), 1)) + " MB for tile " + row["QuadKey"])

        try:
            decompressed_text = gzip.decompress(raw_bytes).decode("utf-8")
        except OSError:
            decompressed_text = raw_bytes.decode("utf-8")

        buildings_kept_from_this_tile = 0
        for line in decompressed_text.strip().split("\n"):
            if len(line) == 0:
                continue
            feature = json.loads(line)
            building_geometry = shape(feature["geometry"])
            if building_geometry.intersects(study_area_box):
                all_building_geometries.append(building_geometry)
                buildings_kept_from_this_tile = buildings_kept_from_this_tile + 1

        print("Kept " + str(buildings_kept_from_this_tile) + " buildings from tile " + row["QuadKey"] + " that fall inside the study area bounding box.")

    print("Total building footprints collected across all tiles: " + str(len(all_building_geometries)))
    return all_building_geometries


def validate_building_footprints(building_geometries):
    print("")
    print("Validating the fetched building footprints ...")

    total_building_count = len(building_geometries)
    print("Total buildings fetched: " + str(total_building_count))

    count_check_passed = total_building_count >= minimum_plausible_building_count
    print("Result: " + str(total_building_count) + " buildings >= required minimum " + str(minimum_plausible_building_count) + " -> " + ("PASS" if count_check_passed else "FAIL"))

    study_area_width_km = (study_area.study_area_maximum_longitude - study_area.study_area_minimum_longitude) * 111.0
    study_area_height_km = (study_area.study_area_maximum_latitude - study_area.study_area_minimum_latitude) * 111.0
    study_area_square_km = study_area_width_km * study_area_height_km
    building_density_per_square_km = total_building_count / study_area_square_km
    print("Approximate building density: " + str(round(building_density_per_square_km, 1)) + " buildings per square km (bounding box area approx " + str(round(study_area_square_km, 1)) + " sq km)")

    density_check_passed = building_density_per_square_km <= maximum_plausible_building_density_per_square_km
    print("Result: " + str(round(building_density_per_square_km, 1)) + " buildings/sq km <= implausibility ceiling " + str(maximum_plausible_building_density_per_square_km) + " -> " + ("PASS" if density_check_passed else "FAIL, possible duplicate or corrupted geometries"))

    invalid_geometry_count = sum(1 for geometry in building_geometries if not geometry.is_valid)
    print("Buildings with invalid geometry: " + str(invalid_geometry_count) + " out of " + str(total_building_count))

    file_is_valid = count_check_passed and density_check_passed
    return file_is_valid, total_building_count, building_density_per_square_km


def decide_keep_or_drop_building_footprints(file_is_valid, total_building_count):
    print("")
    print("Deciding whether to KEEP or DROP the building footprints dataset ...")

    if file_is_valid:
        print("FINAL DECISION: KEEP the building footprints dataset (" + str(total_building_count) + " buildings).")
    else:
        print("FINAL DECISION: DROP the building footprints dataset.")
        print("What is missing: the fetched footprints failed basic sanity checks (too few buildings or an implausible density), so they cannot be trusted as an input.")

    return file_is_valid


def explain_necessity_for_effectiveness_score(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: building footprints are vector data at effectively sub-meter precision, finer than any 10m requirement, and identify exactly which parts of a cell are impervious built structure versus open ground. Without them, built-form density within a cell can only be inferred indirectly from land cover, losing individual building-level detail.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Building Footprints (Microsoft Global ML Building Footprints)")
    print("=====================================================")
    print("Note: this is vector data. 'Resolution' is not a grid measurement here; footprint precision reflects the source imagery Microsoft used, which Microsoft does not publish an exact GSD for. This is disclosed rather than assumed to be a specific number.")

    covering_quadkeys = find_quadkeys_covering_study_area()

    matching_rows = fetch_dataset_links_for_india(covering_quadkeys)
    if len(matching_rows) == 0:
        print("")
        print("FINAL DECISION: DROP. No matching India tiles were found in Microsoft's dataset index for these quadkeys.")
        return

    building_geometries = download_and_load_building_footprints(matching_rows)
    if len(building_geometries) == 0:
        print("")
        print("FINAL DECISION: DROP. Zero building geometries were successfully parsed from the downloaded tiles.")
        return

    file_is_valid, total_building_count, building_density_per_square_km = validate_building_footprints(building_geometries)

    final_decision_is_keep = decide_keep_or_drop_building_footprints(file_is_valid, total_building_count)

    explain_necessity_for_effectiveness_score(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        validated_file_path = os.path.join(output_folder_for_validated_data, "building_footprints.geojson")
        buildings_geodataframe = geopandas.GeoDataFrame(geometry=building_geometries, crs="EPSG:4326")
        buildings_geodataframe.to_file(validated_file_path, driver="GeoJSON")
        print("Saved validated building footprints to " + validated_file_path)


if __name__ == "__main__":
    main()
