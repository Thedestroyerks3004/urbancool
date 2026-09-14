import ee

study_area_minimum_longitude = 80.15
study_area_maximum_longitude = 80.30
study_area_minimum_latitude = 12.75
study_area_maximum_latitude = 13.05

analysis_grid_cell_size_meters = 150

originally_requested_earliest_year = 2015
originally_requested_latest_year = 2026

solidly_covered_earliest_year = 2019
solidly_covered_latest_year = 2026

summer_season_start_month = 3
summer_season_end_month = 6

earth_engine_project_id = "urban-heat-mitigation-502708"

earth_engine_is_ready = False


def make_sure_earth_engine_is_ready():
    global earth_engine_is_ready
    if earth_engine_is_ready:
        return
    print("Connecting to Google Earth Engine using project " + earth_engine_project_id + " ...")
    ee.Initialize(project=earth_engine_project_id)
    earth_engine_is_ready = True
    print("Google Earth Engine connection is ready.")


def get_study_area_rectangle():
    make_sure_earth_engine_is_ready()
    rectangle = ee.Geometry.Rectangle([
        study_area_minimum_longitude,
        study_area_minimum_latitude,
        study_area_maximum_longitude,
        study_area_maximum_latitude,
    ])
    return rectangle


def get_summer_season_date_ranges_for_year_range(first_year, last_year):
    date_ranges = []
    for year in range(first_year, last_year + 1):
        start_date_text = str(year) + "-" + str(summer_season_start_month).zfill(2) + "-01"
        end_date_text = str(year) + "-" + str(summer_season_end_month).zfill(2) + "-30"
        date_ranges.append((year, start_date_text, end_date_text))
    return date_ranges


def get_summer_season_date_ranges_for_originally_requested_window():
    return get_summer_season_date_ranges_for_year_range(originally_requested_earliest_year, originally_requested_latest_year)


def get_summer_season_date_ranges_for_solidly_covered_window():
    return get_summer_season_date_ranges_for_year_range(solidly_covered_earliest_year, solidly_covered_latest_year)
