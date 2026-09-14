import os
import sys
import csv
import datetime
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

output_folder_for_raw_data = os.path.join("D:\\", "Projects", "UC", "data", "raw", "sentinel2_stac_metadata")
output_csv_file_path = os.path.join(output_folder_for_raw_data, "sentinel2_scene_metadata.csv")

stac_search_url = "https://earth-search.aws.element84.com/v1/search"
stac_collection_name = "sentinel-2-c1-l2a"

study_area_west = 79.999
study_area_south = 12.700
study_area_east = 80.301
study_area_north = 13.201

required_maximum_cloud_cover_percentage = 15.0

dry_season_start_month = 1
dry_season_end_month = 3

earliest_year_to_search = 2019
latest_year_to_search = 2026

minimum_scene_count_before_expanding_to_full_year = 5


def search_stac_api_for_date_range(start_date_text, end_date_text):
    request_body = {
        "collections": [stac_collection_name],
        "bbox": [study_area_west, study_area_south, study_area_east, study_area_north],
        "datetime": start_date_text + "T00:00:00Z/" + end_date_text + "T23:59:59Z",
        "query": {"eo:cloud_cover": {"lt": required_maximum_cloud_cover_percentage}},
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        "limit": 100,
    }

    response = requests.post(stac_search_url, json=request_body, timeout=60)
    if response.status_code != 200:
        print("STAC search FAILED for " + start_date_text + " to " + end_date_text + ": HTTP status code " + str(response.status_code) + " -- " + response.text[:300])
        return []

    response_json = response.json()
    features = response_json.get("features", [])
    return features


def search_dry_season_across_all_years():
    print("Searching Earth Search STAC API (" + stac_collection_name + ") for dry-season (Jan-Mar) scenes, years " + str(earliest_year_to_search) + "-" + str(latest_year_to_search) + ", cloud cover < " + str(required_maximum_cloud_cover_percentage) + "% ...")

    all_features = []
    for year in range(earliest_year_to_search, latest_year_to_search + 1):
        start_date_text = str(year) + "-0" + str(dry_season_start_month) + "-01"
        end_date_text = str(year) + "-0" + str(dry_season_end_month) + "-31"
        features_for_year = search_stac_api_for_date_range(start_date_text, end_date_text)
        print("Dry season " + str(year) + ": " + str(len(features_for_year)) + " scenes found with cloud cover < " + str(required_maximum_cloud_cover_percentage) + "%")
        all_features.extend(features_for_year)

    print("Total dry-season scenes found across all years: " + str(len(all_features)))
    return all_features


def search_full_year_across_all_years():
    print("")
    print("Dry-season results were scarce -- expanding the search to the full year for each year ...")

    all_features = []
    for year in range(earliest_year_to_search, latest_year_to_search + 1):
        start_date_text = str(year) + "-01-01"
        end_date_text = str(year) + "-12-31"
        features_for_year = search_stac_api_for_date_range(start_date_text, end_date_text)
        print("Full year " + str(year) + ": " + str(len(features_for_year)) + " scenes found with cloud cover < " + str(required_maximum_cloud_cover_percentage) + "%")
        all_features.extend(features_for_year)

    print("Total full-year scenes found across all years: " + str(len(all_features)))
    return all_features


def extract_scene_metadata_rows(features):
    print("")
    print("Extracting metadata fields for each scene (no image bands are being downloaded) ...")

    rows = []
    for feature in features:
        properties = feature.get("properties", {})
        assets = feature.get("assets", {})

        scene_id = feature.get("id")
        acquisition_datetime = properties.get("datetime")
        cloud_cover_percentage = properties.get("eo:cloud_cover")
        mgrs_tile_id = properties.get("mgrs:grid_square")
        mgrs_utm_zone = properties.get("mgrs:utm_zone")
        mgrs_latitude_band = properties.get("mgrs:latitude_band")
        full_tile_id = str(mgrs_utm_zone) + str(mgrs_latitude_band) + str(mgrs_tile_id)

        thumbnail_link = None
        if "thumbnail" in assets:
            thumbnail_link = assets["thumbnail"].get("href")

        true_color_link = None
        if "visual" in assets:
            true_color_link = assets["visual"].get("href")

        rows.append({
            "scene_id": scene_id,
            "acquisition_date": acquisition_datetime,
            "cloud_cover_percentage": cloud_cover_percentage,
            "tile_id": full_tile_id,
            "thumbnail_link": thumbnail_link,
            "true_color_link": true_color_link,
        })

    print("Extracted metadata for " + str(len(rows)) + " scenes.")
    return rows


def remove_duplicate_scenes(rows):
    seen_scene_ids = set()
    unique_rows = []
    for row in rows:
        if row["scene_id"] not in seen_scene_ids:
            seen_scene_ids.add(row["scene_id"])
            unique_rows.append(row)

    print("After removing duplicates (a scene can appear in more than one date-range query): " + str(len(unique_rows)) + " unique scenes.")
    return unique_rows


def sort_rows_by_cloud_cover(rows):
    rows_with_known_cloud_cover = [row for row in rows if row["cloud_cover_percentage"] is not None]
    rows_with_known_cloud_cover.sort(key=lambda row: row["cloud_cover_percentage"])
    return rows_with_known_cloud_cover


def save_metadata_to_csv(rows):
    os.makedirs(output_folder_for_raw_data, exist_ok=True)
    with open(output_csv_file_path, "w", newline="", encoding="utf-8") as csv_file:
        csv_writer = csv.DictWriter(csv_file, fieldnames=["scene_id", "acquisition_date", "cloud_cover_percentage", "tile_id", "thumbnail_link", "true_color_link"])
        csv_writer.writeheader()
        for row in rows:
            csv_writer.writerow(row)
    print("")
    print("Saved full metadata table to " + output_csv_file_path)


def print_metadata_table(rows):
    print("")
    print("=====================================================")
    print("SCENE METADATA (no bands downloaded -- verification only)")
    print("=====================================================")
    for row in rows:
        print("Scene ID: " + str(row["scene_id"]))
        print("  Acquisition date: " + str(row["acquisition_date"]))
        print("  Cloud cover: " + str(row["cloud_cover_percentage"]) + "%")
        print("  Tile ID: " + str(row["tile_id"]))
        print("  Thumbnail: " + str(row["thumbnail_link"]))
        print("  True color asset: " + str(row["true_color_link"]))
        print("")


def main():
    print("=====================================================")
    print("SEARCHING SENTINEL-2 METADATA ONLY -- NO FULL-RESOLUTION BAND DOWNLOADS")
    print("=====================================================")

    dry_season_features = search_dry_season_across_all_years()

    if len(dry_season_features) >= minimum_scene_count_before_expanding_to_full_year:
        print("Result: " + str(len(dry_season_features)) + " dry-season scenes >= required minimum " + str(minimum_scene_count_before_expanding_to_full_year) + " -> using dry-season results only.")
        all_features = dry_season_features
    else:
        print("Result: " + str(len(dry_season_features)) + " dry-season scenes < required minimum " + str(minimum_scene_count_before_expanding_to_full_year) + " -> expanding search.")
        full_year_features = search_full_year_across_all_years()
        all_features = dry_season_features + full_year_features

    rows = extract_scene_metadata_rows(all_features)
    unique_rows = remove_duplicate_scenes(rows)
    sorted_rows = sort_rows_by_cloud_cover(unique_rows)

    print_metadata_table(sorted_rows)
    save_metadata_to_csv(sorted_rows)

    print("")
    print("STOPPING HERE as requested: no Red, NIR, or other full-resolution band assets have been downloaded.")
    print("Waiting for a specific scene ID to be chosen before fetching anything else.")


if __name__ == "__main__":
    main()
