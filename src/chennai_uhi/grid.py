from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ReferenceGrid:
    epsg: int
    cell_size_m: float
    origin_x: float
    origin_y: float
    width: int
    height: int
    nodata: float = -9999.0

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.origin_x, self.origin_y, self.cell_size_m, self.cell_size_m)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        maxx = self.origin_x + self.width * self.cell_size_m
        miny = self.origin_y - self.height * self.cell_size_m
        return (self.origin_x, miny, maxx, self.origin_y)

    def profile(self, dtype: str = "float32", count: int = 1, nodata: float | None = None) -> dict[str, Any]:
        return {
            "driver": "GTiff",
            "dtype": dtype,
            "count": count,
            "width": self.width,
            "height": self.height,
            "crs": f"EPSG:{self.epsg}",
            "transform": self.transform,
            "nodata": self.nodata if nodata is None else nodata,
            "compress": "deflate",
            "tiled": True,
            "blockxsize": 256,
            "blockysize": 256,
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bounds"] = list(self.bounds)
        return d

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "ReferenceGrid":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            epsg=int(d["epsg"]),
            cell_size_m=float(d["cell_size_m"]),
            origin_x=float(d["origin_x"]),
            origin_y=float(d["origin_y"]),
            width=int(d["width"]),
            height=int(d["height"]),
            nodata=float(d.get("nodata", -9999.0)),
        )


def _snap_down(value: float, cell: float) -> float:
    return np.floor(value / cell) * cell


def _snap_up(value: float, cell: float) -> float:
    return np.ceil(value / cell) * cell


def build_reference_grid(
    bounds_32644: tuple[float, float, float, float],
    cell_size_m: float = 30.0,
    epsg: int = 32644,
    nodata: float = -9999.0,
) -> ReferenceGrid:
    if cell_size_m > 30.0:
        raise ValueError("cell_size_m must be ≤ 30 m")
    minx, miny, maxx, maxy = bounds_32644
    origin_x = float(_snap_down(minx, cell_size_m))
    origin_y = float(_snap_up(maxy, cell_size_m))
    east = float(_snap_up(maxx, cell_size_m))
    south = float(_snap_down(miny, cell_size_m))
    width = int(round((east - origin_x) / cell_size_m))
    height = int(round((origin_y - south) / cell_size_m))
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid grid dimensions: {width}x{height}")
    return ReferenceGrid(
        epsg=epsg,
        cell_size_m=float(cell_size_m),
        origin_x=origin_x,
        origin_y=origin_y,
        width=width,
        height=height,
        nodata=nodata,
    )


def empty_array(grid: ReferenceGrid, dtype=np.float32) -> np.ndarray:
    arr = np.full((grid.height, grid.width), grid.nodata, dtype=dtype)
    return arr
