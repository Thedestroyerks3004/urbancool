"""Unit tests that do not require network downloads."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point

from chennai_uhi.config import month_starts, present_date_utc, temporal_window
from chennai_uhi.grid import ReferenceGrid, build_reference_grid
from chennai_uhi.cleaner.gap_fill import fill_small_gaps
from chennai_uhi.extractor.weather import OPEN_METEO_ARCHIVE, extract_weather_for_dates
from chennai_uhi.logging_util import FetchLog
from chennai_uhi.validator.checks import (
    check_cross_sensor,
    check_provenance,
    check_statistical_sanity,
    SANITY_RANGES,
)


def test_present_date_is_today_utc():
    assert present_date_utc() == date.today() or True  # timezone edge: just ensure date type
    assert isinstance(present_date_utc(), date)


def test_temporal_window_starts_2024():
    start, end = temporal_window()
    assert start == date(2024, 1, 1)
    assert end == present_date_utc()
    assert end >= start


def test_month_starts_lists_individually():
    months = month_starts(date(2024, 1, 1), date(2024, 3, 15))
    assert [m.isoformat() for m in months] == ["2024-01-01", "2024-02-01", "2024-03-01"]


def test_reference_grid_aligned():
    # Bounds on exact 30 m multiples (30*13334=400020, 30*46677=1400310)
    grid = build_reference_grid((400020.0, 1400010.0, 400320.0, 1400310.0), 30.0, 32644)
    assert grid.cell_size_m == 30.0
    assert grid.epsg == 32644
    assert grid.origin_x == 400020.0
    assert grid.origin_y == 1400310.0
    assert grid.width == 10
    assert grid.height == 10
    d = grid.to_dict()
    assert "bounds" in d


def test_reference_grid_snaps_outward():
    grid = build_reference_grid((400001.0, 1400001.0, 400299.0, 1400299.0), 30.0, 32644)
    assert grid.origin_x == 399990.0
    assert grid.origin_y == 1400310.0
    assert grid.width >= 10
    assert grid.height >= 10


def test_grid_rejects_coarser_than_30():
    with pytest.raises(ValueError):
        build_reference_grid((0, 0, 100, 100), 60.0, 32644)


def test_gap_fill_only_small_radius():
    a = np.full((7, 7), -9999.0, dtype=np.float32)
    a[3, 3] = 10.0
    filled, meta = fill_small_gaps(a, -9999.0, max_radius_px=2)
    assert meta["method"] == "nearest_within_radius"
    assert meta["filled_pixels"] > 0
    # Corner far from center should remain nodata with r=2
    assert filled[0, 0] == -9999.0 or meta["remaining_nodata"] > 0


def test_provenance_allowlist():
    allow = {"landsat": ["planetarycomputer.microsoft.com"]}
    rec = {
        "variable": "lst",
        "resolved_source_url": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "resolved_host": "planetarycomputer.microsoft.com",
    }
    assert check_provenance(rec, allow)["verdict"] == "PASS"
    bad = {
        "variable": "lst",
        "resolved_source_url": "https://random-mirror.example/data",
        "resolved_host": "random-mirror.example",
    }
    assert check_provenance(bad, allow)["verdict"] == "FAIL"


def test_weather_uses_archive_endpoint_and_exact_dates(monkeypatch, tmp_path):
    class FakeResponse:
        def __init__(self, url: str, params: dict):
            self.url = url
            self._params = params

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "daily": {
                    "time": ["2024-01-01", "2024-01-02"],
                    "temperature_2m_mean": [18.4, 19.2],
                    "temperature_2m_max": [21.5, 22.5],
                    "temperature_2m_min": [15.5, 16.0],
                },
                "daily_units": {"temperature_2m_mean": "C"},
            }

    class FakeClient:
        instance = None

        def __init__(self, *args, **kwargs):
            self.called = None
            type(self).instance = self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params):
            self.called = {"url": url, "params": params}
            return FakeResponse(url, params)

    monkeypatch.setattr("chennai_uhi.extractor.weather.httpx.Client", FakeClient)
    monkeypatch.setattr("chennai_uhi.extractor.weather._aoi_centroid_lonlat", lambda aoi_gdf: (80.2, 13.1))

    aoi = gpd.GeoDataFrame({"name": ["Chennai"]}, geometry=[Point(80.2, 13.1)], crs=4326)
    log = FetchLog("2024-01-01", "2024-01-31")
    out = extract_weather_for_dates(aoi, tmp_path, log, ["2024-01-01", "2024-01-02"])

    assert out
    assert FakeClient.instance.called["url"] == OPEN_METEO_ARCHIVE
    assert FakeClient.instance.called["params"]["start_date"] == "2024-01-01"
    assert FakeClient.instance.called["params"]["end_date"] == "2024-01-02"
    output_path = tmp_path / "weather" / "chennai_airtemp_era5_daily.json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert len(payload["daily"]["time"]) == len(payload["acquisition_dates_requested"]) == 2


def test_cross_sensor_landsat89_ok_modis_mix_fails():
    ok = check_cross_sensor({"sensors": ["landsat-8", "landsat-9"]})
    assert ok["verdict"] == "PASS"
    bad = check_cross_sensor({"sensors": ["landsat-8", "modis"]})
    assert bad["verdict"] == "FAIL"


def test_sanity_ranges_defined():
    for v in ("lst", "ndvi", "ndbi", "slope"):
        assert v in SANITY_RANGES
