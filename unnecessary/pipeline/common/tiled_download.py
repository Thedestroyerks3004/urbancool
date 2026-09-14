import os
import requests
import rasterio
import rasterio.merge
import ee


def build_tile_rectangles(minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude, tiles_per_side):
    longitude_step = (maximum_longitude - minimum_longitude) / tiles_per_side
    latitude_step = (maximum_latitude - minimum_latitude) / tiles_per_side

    tile_rectangles = []
    for row_index in range(tiles_per_side):
        for column_index in range(tiles_per_side):
            tile_west = minimum_longitude + column_index * longitude_step
            tile_east = minimum_longitude + (column_index + 1) * longitude_step
            tile_south = minimum_latitude + row_index * latitude_step
            tile_north = minimum_latitude + (row_index + 1) * latitude_step
            tile_rectangles.append((tile_west, tile_south, tile_east, tile_north))

    return tile_rectangles


def download_ee_image_in_tiles(image, minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude, scale_in_meters, tiles_per_side, output_folder, base_filename):
    print("Downloading image in " + str(tiles_per_side * tiles_per_side) + " tiles at " + str(scale_in_meters) + "m resolution to stay under Earth Engine's synchronous download size limit ...")

    os.makedirs(output_folder, exist_ok=True)
    tile_rectangles = build_tile_rectangles(minimum_longitude, minimum_latitude, maximum_longitude, maximum_latitude, tiles_per_side)

    maximum_attempts_per_tile = 3

    downloaded_tile_file_paths = []
    for tile_index, (tile_west, tile_south, tile_east, tile_north) in enumerate(tile_rectangles):
        tile_rectangle_geometry = ee.Geometry.Rectangle([tile_west, tile_south, tile_east, tile_north])

        download_url = image.getDownloadURL({
            "scale": scale_in_meters,
            "region": tile_rectangle_geometry,
            "format": "GEO_TIFF",
            "crs": "EPSG:4326",
        })

        tile_downloaded_successfully = False
        for attempt_number in range(1, maximum_attempts_per_tile + 1):
            try:
                response = requests.get(download_url, timeout=300)
            except requests.exceptions.RequestException as request_error:
                print("Tile " + str(tile_index + 1) + " of " + str(len(tile_rectangles)) + ", attempt " + str(attempt_number) + " of " + str(maximum_attempts_per_tile) + " FAILED to connect: " + type(request_error).__name__)
                continue

            if response.status_code != 200:
                print("Tile " + str(tile_index + 1) + " of " + str(len(tile_rectangles)) + ", attempt " + str(attempt_number) + " of " + str(maximum_attempts_per_tile) + " FAILED with HTTP status code " + str(response.status_code) + ": " + response.text[:200])
                continue

            tile_file_path = os.path.join(output_folder, base_filename + "_tile_" + str(tile_index).zfill(3) + ".tif")
            with open(tile_file_path, "wb") as tile_file:
                tile_file.write(response.content)

            downloaded_tile_file_paths.append(tile_file_path)
            print("Tile " + str(tile_index + 1) + " of " + str(len(tile_rectangles)) + " downloaded (" + str(round(os.path.getsize(tile_file_path) / 1024.0, 1)) + " KB)")
            tile_downloaded_successfully = True
            break

        if not tile_downloaded_successfully:
            print("Tile " + str(tile_index + 1) + " of " + str(len(tile_rectangles)) + " permanently FAILED after " + str(maximum_attempts_per_tile) + " attempts.")

    print("Successfully downloaded " + str(len(downloaded_tile_file_paths)) + " out of " + str(len(tile_rectangles)) + " tiles.")

    if len(downloaded_tile_file_paths) == 0:
        print("Tiled download FAILED: zero tiles were successfully downloaded.")
        return None

    print("Mosaicking " + str(len(downloaded_tile_file_paths)) + " tiles into a single raster ...")
    open_tile_datasets = [rasterio.open(path) for path in downloaded_tile_file_paths]
    mosaic_array, mosaic_transform = rasterio.merge.merge(open_tile_datasets)

    output_profile = open_tile_datasets[0].profile.copy()
    output_profile.update(
        height=mosaic_array.shape[1],
        width=mosaic_array.shape[2],
        transform=mosaic_transform,
    )

    for open_dataset in open_tile_datasets:
        open_dataset.close()

    mosaic_file_path = os.path.join(output_folder, base_filename + "_mosaic.tif")
    with rasterio.open(mosaic_file_path, "w", **output_profile) as mosaic_output_file:
        mosaic_output_file.write(mosaic_array)

    print("Saved mosaicked raster to " + mosaic_file_path)

    for tile_file_path in downloaded_tile_file_paths:
        os.remove(tile_file_path)

    return mosaic_file_path
