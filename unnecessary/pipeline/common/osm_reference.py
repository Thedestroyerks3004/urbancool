import requests
from shapely.geometry import Polygon

from pipeline.common import study_area

overpass_api_urls_to_try_in_order = [
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

overpass_request_headers = {
    "User-Agent": "urban-heat-mitigation-research/1.0 (contact: sankarramsu@gmail.com)"
}


def fetch_osm_polygons_for_single_tag(tag_key, tag_value, maximum_features, class_name_for_printing):
    south = study_area.study_area_minimum_latitude
    west = study_area.study_area_minimum_longitude
    north = study_area.study_area_maximum_latitude
    east = study_area.study_area_maximum_longitude

    way_clause = 'way["' + tag_key + '"="' + tag_value + '"](' + str(south) + "," + str(west) + "," + str(north) + "," + str(east) + ");\n"
    overpass_query_text = "[out:json][timeout:55];\n(\n" + way_clause + ");\nout geom " + str(maximum_features) + ";"

    response = None
    for overpass_api_url in overpass_api_urls_to_try_in_order:
        print("Trying Overpass endpoint " + overpass_api_url + " for tag " + tag_key + "=" + tag_value + " ...")
        try:
            candidate_response = requests.post(
                overpass_api_url,
                data={"data": overpass_query_text},
                timeout=70,
                headers=overpass_request_headers,
            )
        except requests.exceptions.RequestException as request_error:
            print("Overpass endpoint " + overpass_api_url + " failed to connect: " + type(request_error).__name__)
            continue

        if candidate_response.status_code == 200:
            print("Overpass endpoint " + overpass_api_url + " responded successfully for tag " + tag_key + "=" + tag_value + ".")
            response = candidate_response
            break
        else:
            print("Overpass endpoint " + overpass_api_url + " returned HTTP status code " + str(candidate_response.status_code) + " for tag " + tag_key + "=" + tag_value + ".")

    if response is None:
        print("Overpass fetch FAILED for tag " + tag_key + "=" + tag_value + " ('" + class_name_for_printing + "'): every endpoint tried was unreachable or rejected the request.")
        return []

    response_json = response.json()
    elements = response_json.get("elements", [])
    print("Overpass returned " + str(len(elements)) + " raw way elements for tag " + tag_key + "=" + tag_value + ".")

    polygons = []
    for element in elements:
        geometry_points = element.get("geometry")
        if geometry_points is None or len(geometry_points) < 4:
            continue
        coordinate_list = [(point["lon"], point["lat"]) for point in geometry_points]
        candidate_polygon = Polygon(coordinate_list)
        if not candidate_polygon.is_valid:
            candidate_polygon = candidate_polygon.buffer(0)
        if candidate_polygon.is_valid and candidate_polygon.area > 0:
            polygons.append(candidate_polygon)

    return polygons


def fetch_osm_polygons_for_tag_list(tag_pairs, maximum_features_per_tag, class_name_for_printing):
    print("Querying OpenStreetMap Overpass API for '" + class_name_for_printing + "' reference polygons, one tag at a time ...")

    all_polygons_for_class = []
    for tag_key, tag_value in tag_pairs:
        polygons_for_this_tag = fetch_osm_polygons_for_single_tag(tag_key, tag_value, maximum_features_per_tag, class_name_for_printing)
        print("Tag " + tag_key + "=" + tag_value + " contributed " + str(len(polygons_for_this_tag)) + " valid polygons to class '" + class_name_for_printing + "'.")
        all_polygons_for_class.extend(polygons_for_this_tag)

    print("Converted a total of " + str(len(all_polygons_for_class)) + " valid polygons for '" + class_name_for_printing + "'.")
    return all_polygons_for_class
