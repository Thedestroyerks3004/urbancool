"""Build a human-readable extractor report from fetch log + manifest."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "logs" / "extractor_fetch_log.json"
MAN = ROOT / "data" / "logs" / "extractor_manifest.json"
OUT = ROOT / "data" / "logs" / "EXTRACTOR_REPORT.md"


DESCRIPTIONS = {
    "aoi": "Full Chennai administrative boundary used as the study AOI (polygon clip, not a hand-drawn box).",
    "dem": "Digital elevation (meters) from Copernicus GLO-30; static terrain surface.",
    "slope": "Terrain slope in degrees derived from the DEM (Horn-style gradient).",
    "landcover": "ESA WorldCover land-cover classes (water, built-up, vegetation, etc.) for masking/context.",
    "population_density": "Gridded population density from WorldPop (closest available release year).",
    "osm_snapshot": "Pinned Geofabrik OpenStreetMap PBF extract (versioned + MD5 verified).",
    "building_footprints": "OSM building polygons clipped to the Chennai AOI.",
    "road_network": "OSM highway network clipped to the Chennai AOI.",
    "building_density": "Per-cell building footprint presence/density proxy (no height).",
    "building_compactness": "Footprint shape compactness (4πA/P²) rasterized to the grid.",
    "street_width": "Road-class typical width proxy (meters) from OSM highway tags — not H/W ratio.",
    "lst": "Land Surface Temperature (Kelvin) — Landsat 8/9 C2 L2 monthly median, QA-masked.",
    "ndvi": "Normalized Difference Vegetation Index — Landsat 8/9 monthly median, QA-masked.",
    "ndbi": "Normalized Difference Built-up Index — Landsat 8/9 monthly median, QA-masked.",
    "landsat_stac_search": "STAC search breadcrumb for Landsat scenes used that month.",
    "air_temperature": "ERA5-based 2 m air temperature at AOI centroid for each Landsat acquisition date.",
    "building_height": "EXCLUDED BY DESIGN — sparse/placeholder height data for this region.",
}


def main() -> None:
    if not LOG.exists():
        raise SystemExit(f"Missing {LOG} — run extractor first")
    log = json.loads(LOG.read_text(encoding="utf-8"))
    man = json.loads(MAN.read_text(encoding="utf-8")) if MAN.exists() else {}
    fetches = log.get("fetches", [])

    by_var: dict[str, list] = defaultdict(list)
    for f in fetches:
        by_var[f.get("variable", "?")].append(f)

    lines: list[str] = []
    lines.append("# Chennai UHI — Extractor Report")
    lines.append("")
    lines.append(f"- **Required time window:** `{log.get('temporal_start_required')}` → `{log.get('temporal_end_required')}` (end = UTC present at run)")
    lines.append(f"- **Log created:** `{log.get('created_at')}`")
    lines.append(f"- **Fetch records:** {log.get('n_records')}")
    lines.append(f"- **Manifest layers:** {man.get('n_layers', 'n/a')}")
    lines.append(f"- **Target CRS:** `{man.get('crs_target', 'EPSG:32644')}`")
    lines.append(f"- **Reference grid:** `{man.get('reference_grid', '')}`")
    lines.append("")
    lines.append("## Status summary")
    lines.append("")
    status_counts: dict[str, int] = defaultdict(int)
    for f in fetches:
        status_counts[f.get("status", "?")] += 1
    for k, v in sorted(status_counts.items()):
        lines.append(f"- `{k}`: **{v}**")
    lines.append("")
    lines.append("## Layers by variable")
    lines.append("")

    for var in sorted(by_var.keys()):
        recs = by_var[var]
        sample = recs[0]
        lines.append(f"### `{var}`")
        lines.append("")
        lines.append(f"**Description:** {DESCRIPTIONS.get(var, '(see source notes)')}")
        lines.append("")
        lines.append(f"- **Source name:** {sample.get('source_name')}")
        lines.append(f"- **Resolved URL / host:** `{sample.get('resolved_source_url')}` / `{sample.get('resolved_host')}`")
        lines.append(f"- **Native CRS:** `{sample.get('native_crs')}`")
        lines.append(f"- **Native resolution (m):** `{sample.get('native_resolution_m')}`")
        lines.append(f"- **Sensors:** `{sample.get('sensors')}`")
        lines.append(f"- **Records:** {len(recs)}")
        # Query params (union of keys)
        qp = sample.get("query_parameters") or {}
        if qp:
            lines.append("- **Query parameters (representative):**")
            lines.append("```json")
            lines.append(json.dumps(qp, indent=2, default=str))
            lines.append("```")
        # Dates
        all_dates = []
        for r in recs:
            all_dates.extend(r.get("data_dates") or [])
            if r.get("last_available_date"):
                all_dates.append(f"last_available={r['last_available_date']}")
        uniq = sorted(set(str(d) for d in all_dates))[:40]
        if uniq:
            lines.append(f"- **Data dates (sample/unique, capped):** {', '.join(uniq)}")
        # Per-record status table for time series
        if len(recs) > 1 and var in {"lst", "ndvi", "ndbi", "landsat_stac_search"}:
            lines.append("")
            lines.append("| layer_id | status | local_path | notes |")
            lines.append("|---|---|---|---|")
            for r in sorted(recs, key=lambda x: x.get("layer_id", "")):
                notes = (r.get("notes") or r.get("error") or "").replace("|", "/")[:80]
                path = r.get("local_path") or ""
                path = Path(path).name if path else ""
                lines.append(f"| `{r.get('layer_id')}` | {r.get('status')} | `{path}` | {notes} |")
        else:
            for r in recs:
                if r.get("local_path"):
                    lines.append(f"- **Local path:** `{r.get('local_path')}`")
                if r.get("notes"):
                    lines.append(f"- **Notes:** {r.get('notes')}")
                if r.get("error"):
                    lines.append(f"- **Error:** {r.get('error')}")
                if r.get("checksum_md5_source") or r.get("checksum_md5_computed"):
                    lines.append(
                        f"- **MD5 source/computed:** `{r.get('checksum_md5_source')}` / `{r.get('checksum_md5_computed')}`"
                    )
                if r.get("checksum_sha256"):
                    lines.append(f"- **SHA-256:** `{r.get('checksum_sha256')[:16]}…`")
        lines.append("")

    lines.append("## Design exclusions")
    lines.append("")
    lines.append("- **building_height / H–W ratio:** not fetched — prior validation showed <5% real OSM height tags and placeholder Microsoft heights for this region.")
    lines.append("")
    lines.append("## Raw artifact inventory")
    lines.append("")
    raw = ROOT / "data" / "raw"
    if raw.exists():
        for p in sorted(raw.rglob("*")):
            if p.is_file() and p.name != ".gitkeep":
                mb = p.stat().st_size / (1024 * 1024)
                lines.append(f"- `{p.relative_to(ROOT)}` ({mb:.2f} MB)")
    lines.append("")

    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
