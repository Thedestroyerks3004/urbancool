import ee

from pipeline.common import study_area
from pipeline.common import thresholds


def add_cloud_free_percentage_property(image, rectangle):
    scl_band = image.select("SCL")
    cloud_shadow_class_value = 3
    medium_cloud_probability_class_value = 8
    high_cloud_probability_class_value = 9
    cirrus_class_value = 10
    is_cloud_free_pixel = scl_band.neq(cloud_shadow_class_value)
    is_cloud_free_pixel = is_cloud_free_pixel.And(scl_band.neq(medium_cloud_probability_class_value))
    is_cloud_free_pixel = is_cloud_free_pixel.And(scl_band.neq(high_cloud_probability_class_value))
    is_cloud_free_pixel = is_cloud_free_pixel.And(scl_band.neq(cirrus_class_value))
    cloud_free_fraction_statistics = is_cloud_free_pixel.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=rectangle,
        scale=20,
        maxPixels=1000000000,
    )
    cloud_free_fraction = ee.Number(cloud_free_fraction_statistics.get("SCL"))
    cloud_free_percentage = cloud_free_fraction.multiply(100)
    return image.set("cloud_free_percentage", cloud_free_percentage)


def fetch_cloud_free_sentinel2_median_composite(band_names, dataset_label):
    print("Fetching Sentinel-2 Level-2A scenes for '" + dataset_label + "' over the solidly-covered window " + str(study_area.solidly_covered_earliest_year) + "-" + str(study_area.solidly_covered_latest_year) + " ...")

    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    solid_window_start_date = str(study_area.solidly_covered_earliest_year) + "-01-01"
    solid_window_end_date = str(study_area.solidly_covered_latest_year) + "-12-31"

    season_date_ranges = study_area.get_summer_season_date_ranges_for_solidly_covered_window()
    season_filters = [ee.Filter.date(start, end) for year, start, end in season_date_ranges]
    combined_season_filter = ee.Filter.Or(*season_filters)

    sentinel2_collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
    sentinel2_collection = sentinel2_collection.filterBounds(rectangle)
    sentinel2_collection = sentinel2_collection.filterDate(solid_window_start_date, solid_window_end_date)
    sentinel2_collection = sentinel2_collection.filter(combined_season_filter)

    candidate_scene_count = sentinel2_collection.size().getInfo()
    print("Candidate Sentinel-2 scenes found for '" + dataset_label + "': " + str(candidate_scene_count))

    if candidate_scene_count == 0:
        print("Fetch failed for '" + dataset_label + "': zero scenes returned.")
        return None, rectangle, 0

    collection_with_cloud_information = sentinel2_collection.map(lambda image: add_cloud_free_percentage_property(image, rectangle))
    qualifying_collection = collection_with_cloud_information.filter(
        ee.Filter.gte("cloud_free_percentage", thresholds.required_cloud_free_pixel_percentage_per_scene)
    )

    qualifying_scene_count = qualifying_collection.size().getInfo()
    print("Scenes passing the " + str(thresholds.required_cloud_free_pixel_percentage_per_scene) + "% cloud-free threshold for '" + dataset_label + "': " + str(qualifying_scene_count))

    if qualifying_scene_count == 0:
        print("Fetch failed for '" + dataset_label + "': zero scenes passed the cloud-free threshold.")
        return None, rectangle, 0

    composite_image = qualifying_collection.select(band_names).median().clip(rectangle)
    print("Built median composite for '" + dataset_label + "' from " + str(qualifying_scene_count) + " qualifying scenes, bands: " + ", ".join(band_names))

    return composite_image, rectangle, qualifying_scene_count
