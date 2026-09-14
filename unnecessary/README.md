# Not part of the running app

Everything in this folder is real, working code and genuine historical write-ups — none
of it is fake or padding. It's kept separate from the project root because none of it
runs when the app runs: the backend serves a pre-built cache
(`backend/cache/feature_stack_10m.npz`), so nothing here executes at request time. This
split exists purely so the root of the repo reads as "the app" (`backend/`, `frontend/`)
without the data-collection history in the way.

## `pipeline/`

The offline scripts that produced the data behind the model, organized by what each one
does:

```
pipeline/
  common/          shared helpers (AOI bounds, thresholds, download/query utilities)
  fetching/        scripts that call an external API (Earth Engine, OSM Overpass)
  derive/          process already-fetched files into NDVI/NDWI/LULC -- no API calls
  health/          check_all_data_health.py, a validation utility
  resolution/      check_resolution_disqualifications.py, a validation utility
```

To refresh the data the model is built from: run the `fetching/` scripts, then `derive/`,
then rebuild the feature stack (`build_feature_stack(force_rebuild=True)` in
`backend/app/feature_stack.py`) and retrain (`backend/app/model.py`). None of this
happens automatically.

## `data-audit/`

Earlier feasibility and pipeline-status write-ups from before the final data run. Mostly
superseded by [`DATA_REPORT.md`](../DATA_REPORT.md) at the project root, kept here for
the record of what was tried and dropped along the way.
