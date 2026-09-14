import os
import sys
import requests
import geopandas
from shapely.geometry import LineString

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.common import study_area
from pipeline.common import osm_reference

output_folder_for_validated_data = os.path.join("D:\\", "Projects", "UC", "data", "validated", "road_network")

road_tags_to_fetch = [
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "residential", "unclassified", "service", "living_street",
]

minimum_plausible_road_count = 20
maximum_plausible_road_length_km_per_square_km = 200.0


def build_quadrant_boxes():
    west = study_area.study_area_minimum_longitude
    east = study_area.study_area_maximum_longitude
    south = study_area.study_area_minimum_latitude
    north = study_area.study_area_maximum_latitude
    mid_longitude = (west + east) / 2.0
    mid_latitude = (south + north) / 2.0

    return [
        (south, west, mid_latitude, mid_longitude),
        (south, mid_longitude, mid_latitude, east),
        (mid_latitude, west, north, mid_longitude),
        (mid_latitude, mid_longitude, north, east),
    ]


def split_box_into_quadrants(south, west, north, east):
    mid_longitude = (west + east) / 2.0
    mid_latitude = (south + north) / 2.0
    return [
        (south, west, mid_latitude, mid_longitude),
        (south, mid_longitude, mid_latitude, east),
        (mid_latitude, west, north, mid_longitude),
        (mid_latitude, mid_longitude, north, east),
    ]


maximum_subdivision_depth = 4


def fetch_road_ways_for_tag_recursive(tag_value, south, west, north, east, depth, quadrant_label):
    road_lines, raw_element_count = fetch_road_ways_for_tag_in_box(tag_value, south, west, north, east)

    if raw_element_count < 5000 or depth >= maximum_subdivision_depth:
        if raw_element_count >= 5000:
            print("  " + quadrant_label + ": " + str(raw_element_count) + " raw elements -- STILL AT CAP after max subdivision depth, genuinely dense area or hitting a hard server limit")
        else:
            print("  " + quadrant_label + ": " + str(raw_element_count) + " raw elements")
        return road_lines

    print("  " + quadrant_label + ": " + str(raw_element_count) + " raw elements (hit cap, subdividing further) ...")
    sub_boxes = split_box_into_quadrants(south, west, north, east)
    all_sub_lines = []
    for sub_index, (sub_south, sub_west, sub_north, sub_east) in enumerate(sub_boxes):
        sub_lines = fetch_road_ways_for_tag_recursive(tag_value, sub_south, sub_west, sub_north, sub_east, depth + 1, quadrant_label + "." + str(sub_index + 1))
        all_sub_lines.extend(sub_lines)
    return all_sub_lines


def fetch_road_ways_for_tag_in_box(tag_value, south, west, north, east):
    way_clause = 'way["highway"="' + tag_value + '"](' + str(south) + "," + str(west) + "," + str(north) + "," + str(east) + ");\n"
    overpass_query_text = "[out:json][timeout:55];\n(\n" + way_clause + ");\nout geom 5000;"

    for overpass_api_url in osm_reference.overpass_api_urls_to_try_in_order:
        try:
            response = requests.post(overpass_api_url, data={"data": overpass_query_text}, timeout=70, headers=osm_reference.overpass_request_headers)
        except requests.exceptions.RequestException:
            continue

        if response.status_code == 200:
            elements = response.json().get("elements", [])
            road_lines = []
            for element in elements:
                geometry_points = element.get("geometry")
                if geometry_points is None or len(geometry_points) < 2:
                    continue
                coordinate_list = [(point["lon"], point["lat"]) for point in geometry_points]
                road_lines.append({"geometry": LineString(coordinate_list), "highway_type": tag_value, "osm_way_id": element.get("id")})
            return road_lines, len(elements)

    return [], 0


def fetch_road_ways_for_tag(tag_value):
    print("Fetching highway=" + tag_value + " across 4 AOI quadrants (to stay well under any single-query Overpass truncation limit) ...")

    quadrant_boxes = build_quadrant_boxes()
    all_lines_for_tag = []
    for quadrant_index, (south, west, north, east) in enumerate(quadrant_boxes):
        road_lines = fetch_road_ways_for_tag_recursive(tag_value, south, west, north, east, depth=1, quadrant_label="Q" + str(quadrant_index + 1))
        all_lines_for_tag.extend(road_lines)

    return all_lines_for_tag


def remove_duplicate_ways_by_id(road_features):
    seen_way_ids = set()
    unique_features = []
    for feature in road_features:
        way_id = feature.get("osm_way_id")
        if way_id is not None and way_id in seen_way_ids:
            continue
        if way_id is not None:
            seen_way_ids.add(way_id)
        unique_features.append(feature)
    return unique_features


def fetch_all_road_ways():
    print("Fetching OpenStreetMap road network for the Anna University / OMR-ECR study area, one road class at a time, quadrant by quadrant ...")

    all_road_features = []
    for tag_value in road_tags_to_fetch:
        road_lines = fetch_road_ways_for_tag(tag_value)
        print("Total for highway=" + tag_value + " before dedup: " + str(len(road_lines)))
        all_road_features.extend(road_lines)

    before_dedup_count = len(all_road_features)
    all_road_features = remove_duplicate_ways_by_id(all_road_features)
    print("")
    print("Total road segments before dedup: " + str(before_dedup_count) + ", after removing ways duplicated across quadrant boundaries: " + str(len(all_road_features)))
    return all_road_features


def validate_road_network(road_features):
    print("")
    print("Validating the fetched road network ...")

    total_road_count = len(road_features)
    count_check_passed = total_road_count >= minimum_plausible_road_count
    print("Result: " + str(total_road_count) + " road segments >= required minimum " + str(minimum_plausible_road_count) + " -> " + ("PASS" if count_check_passed else "FAIL"))

    total_length_km = 0.0
    for feature in road_features:
        line = feature["geometry"]
        length_degrees = line.length
        length_km = length_degrees * 111.0
        total_length_km = total_length_km + length_km

    study_area_width_km = (study_area.study_area_maximum_longitude - study_area.study_area_minimum_longitude) * 111.0
    study_area_height_km = (study_area.study_area_maximum_latitude - study_area.study_area_minimum_latitude) * 111.0
    study_area_square_km = study_area_width_km * study_area_height_km
    road_density_km_per_square_km = total_length_km / study_area_square_km

    print("Total road length: " + str(round(total_length_km, 1)) + " km across " + str(round(study_area_square_km, 1)) + " sq km (density " + str(round(road_density_km_per_square_km, 1)) + " km/sq km)")
    density_check_passed = road_density_km_per_square_km <= maximum_plausible_road_length_km_per_square_km
    print("Result: " + str(round(road_density_km_per_square_km, 1)) + " km/sq km <= implausibility ceiling " + str(maximum_plausible_road_length_km_per_square_km) + " -> " + ("PASS" if density_check_passed else "FAIL"))

    invalid_geometry_count = sum(1 for feature in road_features if not feature["geometry"].is_valid)
    print("Segments with invalid geometry: " + str(invalid_geometry_count) + " out of " + str(total_road_count))

    file_is_valid = count_check_passed and density_check_passed
    return file_is_valid, total_road_count, road_density_km_per_square_km


def decide_keep_or_drop(file_is_valid, total_road_count):
    print("")
    if file_is_valid:
        print("FINAL DECISION: KEEP the road network dataset (" + str(total_road_count) + " segments).")
    else:
        print("FINAL DECISION: DROP the road network dataset. Sanity checks failed.")
    return file_is_valid


def explain_necessity(final_decision_is_keep):
    print("")
    if final_decision_is_keep:
        print("Necessity check: road network is vector data at sub-meter precision (finer than 10m, no downscaling involved). It identifies impervious linear infrastructure and street-canyon geometry per cell, feeding built-form density alongside the building footprints and LULC layers. Without it, road-driven heat retention within a cell cannot be distinguished from building-driven heat retention.")
    else:
        print("Necessity check skipped because the dataset was dropped.")


def main():
    print("=====================================================")
    print("FETCHING AND VALIDATING: Road Network (OpenStreetMap)")
    print("=====================================================")
    print("Vector data -- sub-meter geometric precision, genuinely finer than 10m, no downscaling of any kind involved.")

    road_features = fetch_all_road_ways()
    if len(road_features) == 0:
        print("")
        print("FINAL DECISION: DROP. Zero road segments were fetched.")
        return

    file_is_valid, total_road_count, road_density = validate_road_network(road_features)
    final_decision_is_keep = decide_keep_or_drop(file_is_valid, total_road_count)
    explain_necessity(final_decision_is_keep)

    if final_decision_is_keep:
        os.makedirs(output_folder_for_validated_data, exist_ok=True)
        output_file_path = os.path.join(output_folder_for_validated_data, "road_network.geojson")
        geometries = [feature["geometry"] for feature in road_features]
        highway_types = [feature["highway_type"] for feature in road_features]
        roads_geodataframe = geopandas.GeoDataFrame({"highway_type": highway_types}, geometry=geometries, crs="EPSG:4326")
        roads_geodataframe.to_file(output_file_path, driver="GeoJSON")
        print("Saved validated road network to " + output_file_path)


if __name__ == "__main__":
    main()
