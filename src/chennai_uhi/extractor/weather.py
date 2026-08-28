"""Weather / air-temperature for each Landsat acquisition date.

Uses Open-Meteo Historical API (ERA5-land backed) — authoritative host allow-listed.
Fetches 2 m temperature for the exact scene dates recorded by the Landsat extractor,
not merely "the same year."
"""

from __future__ import annotations

import calendar
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from chennai_uhi.logging_util import FetchLog, sha256_file, utc_now_iso

logger = logging.getLogger(__name__)

OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"


def _aoi_centroid_lonlat(aoi_gdf) -> tuple[float, float]:
    from shapely.ops import unary_union

    c = unary_union(aoi_gdf.to_crs(4326).geometry.values).centroid
    return float(c.x), float(c.y)


def _normalize_acquisition_dates(acquisition_dates: list[str]) -> list[str]:
    """Accept month strings and exact dates, returning ISO dates usable by Open-Meteo."""
    dates: set[str] = set()
    for raw in acquisition_dates or []:
        if not raw:
            continue
        s = str(raw).strip()
        if not s:
            continue
        if len(s) == 7 and s[4] == "-":
            try:
                year, month = map(int, s.split("-"))
                last_day = calendar.monthrange(year, month)[1]
                for day in range(1, last_day + 1):
                    dates.add(date(year, month, day).isoformat())
            except ValueError:
                continue
            continue
        try:
            dates.add(date.fromisoformat(s[:10]).isoformat())
        except ValueError:
            continue
    return sorted(dates)


def extract_weather_for_dates(
    aoi_gdf,
    raw_dir: Path,
    fetch_log: FetchLog,
    acquisition_dates: list[str],
) -> list[dict[str, Any]]:
    """Fetch daily mean/max/min 2 m air temperature for each unique acquisition date."""
    out_dir = raw_dir / "weather"
    out_dir.mkdir(parents=True, exist_ok=True)
    dates = _normalize_acquisition_dates(acquisition_dates)
    if not dates:
        fetch_log.add(
            layer_id="chennai_weather_airtemp",
            variable="air_temperature",
            source_name="Open-Meteo ERA5 archive",
            query_parameters={},
            resolved_source_url=OPEN_METEO_ARCHIVE,
            status="skipped",
            notes="No Landsat acquisition dates available yet",
        )
        return []

    lon, lat = _aoi_centroid_lonlat(aoi_gdf)
    # Open-Meteo allows range requests — chunk by year if needed
    start, end = dates[0], dates[-1]
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start,
        "end_date": end,
        "daily": "temperature_2m_mean,temperature_2m_max,temperature_2m_min",
        "timezone": "Asia/Kolkata",
        "models": "era5",
    }
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        r = client.get(OPEN_METEO_ARCHIVE, params=params)
        r.raise_for_status()
        payload = r.json()

    out_path = out_dir / "chennai_airtemp_era5_daily.json"
    # Keep only dates that match acquisition dates
    daily = payload.get("daily", {})
    time = daily.get("time", [])
    keep_idx = [i for i, t in enumerate(time) if t in set(dates)]
    filtered = {
        "source": "Open-Meteo Historical API (ERA5)",
        "endpoint": OPEN_METEO_ARCHIVE,
        "query": params,
        "centroid_lonlat": [lon, lat],
        "acquisition_dates_requested": dates,
        "daily": {
            "time": [time[i] for i in keep_idx],
            "temperature_2m_mean": [daily.get("temperature_2m_mean", [None])[i] for i in keep_idx],
            "temperature_2m_max": [daily.get("temperature_2m_max", [None])[i] for i in keep_idx],
            "temperature_2m_min": [daily.get("temperature_2m_min", [None])[i] for i in keep_idx],
        },
        "units": payload.get("daily_units", {}),
        "fetched_at": utc_now_iso(),
    }
    out_path.write_text(json.dumps(filtered, indent=2) + "\n", encoding="utf-8")

    last_avail = filtered["daily"]["time"][-1] if filtered["daily"]["time"] else None
    fetch_log.add(
        layer_id="chennai_weather_airtemp",
        variable="air_temperature",
        source_name="Open-Meteo Historical API (ERA5)",
        query_parameters=params,
        resolved_source_url=str(r.url),
        fetch_timestamp=utc_now_iso(),
        data_dates=filtered["daily"]["time"],
        last_available_date=last_avail,
        local_path=str(out_path),
        checksum_sha256=sha256_file(out_path),
        native_crs="EPSG:4326",
        native_resolution_m=None,
        notes="Point series at AOI centroid for LST calibration; one record per Landsat acquisition date",
        status="ok",
    )
    return [
        {
            "layer_id": "chennai_weather_airtemp",
            "variable": "air_temperature",
            "unit": "celsius",
            "path": str(out_path),
            "data_type": "timeseries",
        }
    ]
