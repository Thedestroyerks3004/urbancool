import os
import sys
import rasterio
import numpy
import geopandas

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

validated_root = os.path.join("D:\\", "Projects", "UC", "data", "validated")


def check_one_raster_file(file_path):
    try:
        with rasterio.open(file_path) as raster_dataset:
            band_values = raster_dataset.read()
            width = raster_dataset.width
            height = raster_dataset.height
            band_count = raster_dataset.count
    except Exception as error:
        return False, "cannot open: " + type(error).__name__ + ": " + str(error)

    if width == 0 or height == 0:
        return False, "zero-size raster"

    all_nan_or_zero = numpy.all(numpy.isnan(band_values)) or numpy.all(band_values == 0)
    if all_nan_or_zero:
        return False, "entirely empty (all NaN or all zero)"

    file_size_bytes = os.path.getsize(file_path)
    if file_size_bytes < 200:
        return False, "suspiciously tiny file (" + str(file_size_bytes) + " bytes)"

    return True, str(width) + "x" + str(height) + "x" + str(band_count) + " bands, " + str(round(file_size_bytes / 1024.0, 1)) + " KB"


def check_raster_folder(folder_name, filename_pattern):
    folder_path = os.path.join(validated_root, folder_name)
    print("")
    print("-----------------------------------------------------")
    print("Checking " + folder_name + " ...")
    print("-----------------------------------------------------")

    if not os.path.exists(folder_path):
        print("Result: folder does not exist -> DATASET MISSING")
        return {"dataset": folder_name, "expected": 0, "healthy": 0, "unhealthy": 0, "missing_months": [], "unhealthy_files": []}

    month_folders = sorted([name for name in os.listdir(folder_path) if os.path.isdir(os.path.join(folder_path, name))])

    healthy_count = 0
    unhealthy_files = []
    for month_label in month_folders:
        month_folder = os.path.join(folder_path, month_label)
        candidate_files = [name for name in os.listdir(month_folder) if name.endswith(".tif")]
        if len(candidate_files) == 0:
            unhealthy_files.append((month_label, "no .tif file found in folder"))
            continue
        file_path = os.path.join(month_folder, candidate_files[0])
        is_healthy, detail = check_one_raster_file(file_path)
        if is_healthy:
            healthy_count = healthy_count + 1
        else:
            unhealthy_files.append((month_label, detail))

    print("Months present: " + str(len(month_folders)) + ", healthy: " + str(healthy_count) + ", unhealthy: " + str(len(unhealthy_files)))
    for month_label, detail in unhealthy_files:
        print("  UNHEALTHY: " + month_label + " -- " + detail)

    return {"dataset": folder_name, "expected": len(month_folders), "healthy": healthy_count, "unhealthy": len(unhealthy_files), "unhealthy_files": unhealthy_files}


def check_flat_raster_folder(folder_name):
    folder_path = os.path.join(validated_root, folder_name)
    print("")
    print("-----------------------------------------------------")
    print("Checking " + folder_name + " ...")
    print("-----------------------------------------------------")

    if not os.path.exists(folder_path):
        print("Result: folder does not exist -> DATASET MISSING")
        return {"dataset": folder_name, "expected": 0, "healthy": 0, "unhealthy": 0, "unhealthy_files": []}

    tif_files = [name for name in os.listdir(folder_path) if name.endswith(".tif")]
    healthy_count = 0
    unhealthy_files = []
    for file_name in tif_files:
        file_path = os.path.join(folder_path, file_name)
        is_healthy, detail = check_one_raster_file(file_path)
        if is_healthy:
            healthy_count = healthy_count + 1
        else:
            unhealthy_files.append((file_name, detail))
        print(("HEALTHY: " if is_healthy else "UNHEALTHY: ") + file_name + " -- " + detail)

    print("Files present: " + str(len(tif_files)) + ", healthy: " + str(healthy_count) + ", unhealthy: " + str(len(unhealthy_files)))
    return {"dataset": folder_name, "expected": len(tif_files), "healthy": healthy_count, "unhealthy": len(unhealthy_files), "unhealthy_files": unhealthy_files}


def check_vector_file(folder_name, file_name, expected_minimum_features):
    folder_path = os.path.join(validated_root, folder_name)
    file_path = os.path.join(folder_path, file_name)
    print("")
    print("-----------------------------------------------------")
    print("Checking " + folder_name + "/" + file_name + " ...")
    print("-----------------------------------------------------")

    if not os.path.exists(file_path):
        print("Result: file does not exist -> DATASET MISSING")
        return

    try:
        geodataframe = geopandas.read_file(file_path)
    except Exception as error:
        print("Result: FAILED TO OPEN -- " + type(error).__name__ + ": " + str(error))
        return

    feature_count = len(geodataframe)
    invalid_count = (~geodataframe.geometry.is_valid).sum()
    print("Feature count: " + str(feature_count) + " (expected at least " + str(expected_minimum_features) + ")")
    print("Invalid geometries: " + str(invalid_count))

    is_healthy = feature_count >= expected_minimum_features and invalid_count < feature_count * 0.05
    print("Result: " + ("HEALTHY" if is_healthy else "UNHEALTHY -- feature count too low or too many invalid geometries"))


def main():
    print("=====================================================")
    print("DATA HEALTH CHECK -- opening and inspecting every validated file, not just trusting old logs")
    print("=====================================================")

    monthly_results = []
    monthly_results.append(check_raster_folder("sentinel2_10m_monthly_least_cloudy", "sentinel2_10m_least_cloudy_"))
    monthly_results.append(check_raster_folder("lulc_10m_monthly", "lulc_10m_"))
    monthly_results.append(check_raster_folder("lst_30m_monthly", "lst_30m_"))
    monthly_results.append(check_flat_raster_folder("ndvi_10m_monthly"))
    monthly_results.append(check_flat_raster_folder("water_ndwi_10m_monthly"))

    check_vector_file("building_footprints", "building_footprints.geojson", expected_minimum_features=50)
    check_vector_file("road_network", "road_network.geojson", expected_minimum_features=20)

    print("")
    print("=====================================================")
    print("SUMMARY")
    print("=====================================================")
    for result in monthly_results:
        print(result["dataset"] + ": " + str(result["healthy"]) + " healthy / " + str(result["expected"]) + " present")


if __name__ == "__main__":
    main()
