from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = PACKAGE_ROOT / "config" / "settings.yaml"
DEFAULT_ALLOWLIST = PACKAGE_ROOT / "config" / "allowlist.yaml"


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def present_date_utc() -> date:
    return datetime.now(timezone.utc).date()


def temporal_window(settings: dict[str, Any] | None = None) -> tuple[date, date]:
    settings = settings or load_yaml(DEFAULT_SETTINGS)
    start = date.fromisoformat(settings["project"]["temporal_start"])
    end = present_date_utc()
    if end < start:
        raise ValueError(f"Present date {end} is before temporal_start {start}")
    return start, end


def month_starts(start: date, end: date) -> list[date]:
    months: list[date] = []
    y, m = start.year, start.month
    while True:
        d = date(y, m, 1)
        if d > end:
            break
        months.append(d)
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return months


def resolve_paths(settings: dict[str, Any], work_root: Path | None = None) -> dict[str, Path]:
    root = work_root or PACKAGE_ROOT
    paths = settings["paths"]
    resolved = {k: (root / v).resolve() for k, v in paths.items() if k != "root"}
    resolved["root"] = (root / paths["root"]).resolve()
    for p in resolved.values():
        p.mkdir(parents=True, exist_ok=True)
    return resolved


def load_config(work_root: Path | None = None) -> dict[str, Any]:
    settings = load_yaml(DEFAULT_SETTINGS)
    allowlist = load_yaml(DEFAULT_ALLOWLIST)
    start, end = temporal_window(settings)
    paths = resolve_paths(settings, work_root)
    return {
        "settings": settings,
        "allowlist": allowlist,
        "temporal_start": start.isoformat(),
        "temporal_end": end.isoformat(),
        "temporal_end_note": (
            "temporal_end is the UTC date at pipeline start; "
            "per-source last_available_date is recorded when source lags today."
        ),
        "paths": {k: str(v) for k, v in paths.items()},
        "_paths": paths,
        "_settings": settings,
        "_allowlist": allowlist,
    }


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, default=str)
        f.write("\n")
