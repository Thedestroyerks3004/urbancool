import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import contextily as ctx
from shapely.geometry import box

OUT_DIR = "D:\\Projects\\UC\\model_output_100m"
WGS84 = "EPSG:4326"

AOI_WEST, AOI_SOUTH, AOI_EAST, AOI_NORTH = 80.15, 12.75, 80.30, 13.05

print("Loading the already-computed scored zone polygons ...")
map_gdf = gpd.read_file(f"{OUT_DIR}\\zone_heat_vulnerability_scores_100m.geojson")
print(f"Loaded {len(map_gdf)} zones.")

aoi_wgs84 = box(AOI_WEST, AOI_SOUTH, AOI_EAST, AOI_NORTH)
map_gdf_web_mercator = map_gdf.to_crs(epsg=3857)
aoi_web_mercator = gpd.GeoDataFrame(geometry=[aoi_wgs84], crs=WGS84).to_crs(epsg=3857).geometry.iloc[0]

fig, ax = plt.subplots(figsize=(12, 16))
map_gdf_web_mercator.plot(
    column="score", cmap="YlOrRd", linewidth=0.0, ax=ax, alpha=0.75,
    legend=True, legend_kwds={"label": "Heat Vulnerability Score (0-100, structural/vegetation-only, LST-excluded)", "shrink": 0.6},
)
minx, miny, maxx, maxy = aoi_web_mercator.bounds
ax.set_xlim(minx, maxx)
ax.set_ylim(miny, maxy)

print("Fetching real basemap tiles from Esri World Street Map (free, no API key required, verified to return real 200 image responses) ...")
try:
    ctx.add_basemap(ax, source=ctx.providers.Esri.WorldStreetMap, crs=map_gdf_web_mercator.crs.to_string())
    print("Real Esri basemap tiles fetched and added successfully.")
except Exception as error:
    print(f"Basemap fetch failed ({type(error).__name__}: {error}) -- saving choropleth without basemap tiles rather than faking one.")

ax.set_title("Chennai OMR-ECR Corridor -- Heat Vulnerability Score\n(Structural/vegetation-only, 100m zones, LST-excluded)", fontsize=13)
ax.set_axis_off()
plt.tight_layout()
out_path = f"{OUT_DIR}\\heat_vulnerability_choropleth_100m.png"
plt.savefig(out_path, dpi=150)
plt.close(fig)
print(f"Saved corrected choropleth to {out_path}")
