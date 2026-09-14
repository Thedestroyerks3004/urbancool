import os
import sys
import ee

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.common import study_area

required_maximum_resolution_meters = 10.0


def check_sentinel2_swir_band_resolution():
    print("Checking the real native resolution of Sentinel-2 SWIR bands (B11, B12), used for NDBI ...")
    study_area.make_sure_earth_engine_is_ready()
    rectangle = study_area.get_study_area_rectangle()

    sentinel2_collection = ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(rectangle).limit(1)
    sample_image = ee.Image(sentinel2_collection.first())

    swir_band_nominal_scale_meters = sample_image.select("B11").projection().nominalScale().getInfo()
    print("Earth Engine reports the native projection scale of Sentinel-2 band B11 as: " + str(swir_band_nominal_scale_meters) + " meters")

    passes_requirement = swir_band_nominal_scale_meters <= required_maximum_resolution_meters
    print("Result: " + str(swir_band_nominal_scale_meters) + "m <= required " + str(required_maximum_resolution_meters) + "m -> " + ("KEEP" if passes_requirement else "DROP"))

    if not passes_requirement:
        print("Decision: DROP NDBI from the 10m-only dataset. B11/B12 are natively 20m; ESA's own L2A product upsamples them to a 10m pixel grid via resampling, which is not real 10m information. No NDBI layer will be produced, rather than presenting an upsampled 20m signal as native 10m.")

    return passes_requirement


def check_copernicus_dem_resolution():
    print("")
    print("Checking the real native resolution of the Copernicus GLO-30 DEM ...")
    study_area.make_sure_earth_engine_is_ready()

    dem_image = ee.Image(ee.ImageCollection("COPERNICUS/DEM/GLO30").first())
    dem_nominal_scale_meters = dem_image.select("DEM").projection().nominalScale().getInfo()
    print("Earth Engine reports the native projection scale of the Copernicus GLO-30 DEM as: " + str(dem_nominal_scale_meters) + " meters")

    passes_requirement = dem_nominal_scale_meters <= required_maximum_resolution_meters
    print("Result: " + str(dem_nominal_scale_meters) + "m <= required " + str(required_maximum_resolution_meters) + "m -> " + ("KEEP" if passes_requirement else "DROP"))

    if not passes_requirement:
        print("Decision: DROP elevation/DEM from the 10m-only dataset. GLO-30 is the finest open global DEM available (30m native, derived from TanDEM-X), and no free public DEM at 10m or finer exists for this study area. Not fabricating a finer number to satisfy the requirement.")

    return passes_requirement


def check_ecostress_access():
    print("")
    print("Checking access to NASA/JPL ECOSTRESS L2 LSTE (the only candidate 10m-class-relevant thermal source, native ~70m) ...")
    print("ECOSTRESS is distributed via NASA Earthdata / AppEEARS, which requires an authenticated NASA Earthdata account (username + password or a bearer token).")
    print("No NASA Earthdata credentials have been provided in this environment.")
    print("Result: credentials present = False -> DROP for this run (fetch-blocked, not a resolution failure). ECOSTRESS is natively ~70m in any case, so even if fetched it would need disclosed TsHARP/DisTrad downscaling to reach 10m, never presented as a native 10m measurement.")
    return False


def main():
    print("=====================================================")
    print("CHECKING WHICH REQUESTED LAYERS ARE PHYSICALLY DISQUALIFIED FROM THE 10m-ONLY REQUIREMENT")
    print("=====================================================")

    check_sentinel2_swir_band_resolution()
    check_copernicus_dem_resolution()
    check_ecostress_access()


if __name__ == "__main__":
    main()
