# Data Feasibility Audit — Resolution-Honesty Pass
**Study area:** Chennai Metropolitan Area, OMR-Pallikaranai corridor (12.7-13.2N, 80.0-80.3E) | **Target grid:** 150m where genuinely native-supportable | **Window:** 2015-2026

**Headline honesty check:** there is no single source among these 10 that is natively gridded at 150m for *every* variable. Optical/vector layers (Sentinel-2, Landsat, OSM, WorldPop, JRC GSW) genuinely support ≤150m. Reanalysis- and station-based layers (air temperature, wind) and polygon-based layers (Census demographics) do **not** — and no amount of interpolation makes them native at that scale. Those are flagged below rather than dressed up.

---

## 1. Land Surface Temperature (LST)
**Intended use:** Per-cell surface temperature for heat exposure ranking.
**Primary source:** Landsat 8/9 Collection 2 Level-2, USGS/NASA. Access: USGS EarthExplorer, or `LANDSAT/LC08_L2`/`LC09_L2` via Google Earth Engine.
**TRUE native resolution:** Thermal bands 10/11 are natively sensed at **100m** by the TIRS instrument; USGS resamples/distributes the Level-2 ST product at **30m** by co-registering with the 30m OLI reflective bands — this is a registration/reprojection choice, not a genuine 100m→30m information gain. Report 30m as the *distributed* resolution but disclose the 100m sensing origin.
**Temporal coverage:** L8 since 2013-04, L9 since 2021-09 — full 2015-2026 coverage. Revisit 16 days/satellite, ~8 days combined.
**Documented limitations (USGS Landsat 8-9 C2 L2 Data Format Guide):** thermal unusable under cloud; single ~10:30 local overpass; TIRS has known stray-light calibration issues (L8 TIRS Band 11 specifically flagged as less reliable — USGS recommends Band 10 preferentially).
**Resolution_fit:** 30m distributed resolution aggregates cleanly into 150m (5x5 block mean) with no downscaling required — this is a genuine multi-look aggregation, not interpolation.
**Verdict:** useful_with_caveats (use Band 10 only; composite cloud-free scenes).
**Final recommendation:** Landsat 8/9 C2 L2, Band 10. **Resolution honesty check: genuinely native at 150m via straightforward spatial aggregation of a 30m distributed (100m-sensed) product — not statistically downscaled.**

---

## 2. Land Use / Land Cover (LULC)
**Intended use:** Surface-type composition per cell.
**Primary source:** Sentinel-2 MSI, ESA/Copernicus. Access: Copernicus Data Space Ecosystem, or `COPERNICUS/S2_SR_HARMONIZED` via GEE, self-classified.
**TRUE native resolution:** 10m for B2/B3/B4/B8 (used for classification); note B11/B12 SWIR bands used in some LULC workflows are natively 20m, upsampled to 10m by ESA for the L2A product — flag if SWIR bands are used, since that specific "10m" is interpolated.
**Temporal coverage:** S2A since 2015-06, S2B since 2017-03 — full window covered. 5-day combined revisit.
**Documented limitations (ESA Sentinel-2 User Handbook):** L2A atmospheric correction (Sen2Cor) can introduce artifacts over bright/urban surfaces; classification accuracy is user-dependent, not a validated ESA product.
**Resolution_fit:** 10m native (for the bands actually used) aggregates into 150m with no downscaling needed.
**Verdict:** useful_with_caveats (classification pipeline, training data, and accuracy assessment are the project's own responsibility).
**Final recommendation:** Sentinel-2 10m bands, self-classified. **Resolution honesty check: native 10m sensor data aggregated to 150m — genuine, provided SWIR (20m-native) bands are excluded or their upsampled origin is disclosed if included.**

---

## 3. Vegetation Index (NDVI)
**Intended use:** Cooling-capacity proxy per cell.
**Primary source:** Sentinel-2 B4 (Red)/B8 (NIR), ESA/Copernicus, same access as above.
**TRUE native resolution:** 10m — both B4 and B8 are natively 10m sensors on the MSI instrument (no upsampling involved, unlike SWIR bands).
**Temporal coverage:** 2015-06 to present, 5-day revisit — full window covered.
**Documented limitations:** cloud/haze contamination requires masking (ESA-documented QA60/SCL bands); no upsampling caveat applies here since both bands are natively 10m.
**Resolution_fit:** Genuinely native 10m, clean aggregation to 150m.
**Verdict:** useful.
**Final recommendation:** Sentinel-2 NDVI. **Resolution honesty check: fully native 10m sensor measurement aggregated to 150m — no interpolation anywhere in the chain.**

---

## 4. Population density
**Intended use:** Population-weighting for vulnerability ranking.
**Primary source:** WorldPop, University of Southampton. Access: WorldPop Hub / GEE `WorldPop/GP/100m/pop`.
**TRUE native resolution:** WorldPop's own methodology documentation states the 100m output is a **modeled, dasymetric redistribution** of coarser census-unit counts (India: Census 2011 sub-district/town level, far coarser than 100m) using a random-forest-weighted ancillary layer (building footprints, land cover, roads, nightlights). **The 100m grid is not a direct observation — it is a statistically constructed surface.** This must be flagged explicitly; vendor framing as "100m population" understates that it's model output, not a census-native measurement.
**Temporal coverage:** Annual layers 2000-2020 in the core product; later years exist in some WorldPop product lines but are not guaranteed at the same validation standard — confirm vintage before use for 2021-2026.
**Documented limitations (WorldPop methodology papers, e.g. Stevens et al. 2015 dasymetric approach):** accuracy degrades where ancillary layers (building footprints) are themselves incomplete — directly relevant to OMR where OSM building coverage is uneven; model trained on 2011-era census inputs, so recent densification is not directly observed, only inferred via ancillary proxies.
**Resolution_fit vs 150m:** 100m native model grid aggregates upward into 150m cells without further interpolation — the aggregation step itself is honest, but the underlying 100m layer already carries model uncertainty that propagates forward.
**Verdict:** useful_with_caveats.
**Final recommendation:** WorldPop 100m aggregated to 150m. **Resolution honesty check: NOT a native population measurement at any resolution — it is a dasymetric-modeled surface built from a coarser census input plus ancillary covariates. Present it explicitly as "model-derived population estimate," never as "measured population density."**

---

## 5. Demographic vulnerability indicators (age structure, occupation)
**Intended use:** Identify elderly/outdoor-worker concentrations per cell.
**Primary source:** Census of India 2011, Registrar General & Census Commissioner of India.
**TRUE native resolution:** None — Census data is tabulated at **ward/town polygon level**, not a grid. There is no "native resolution" figure to report; any grid value is a downstream construction.
**Temporal coverage:** Single snapshot, 2011-02. The 2021 Census was postponed and has not been conducted as of 2026 — this is a **15-year-old data gap** against the stated 2015-2026 window, confirmed via Census of India's own public schedule notices.
**Documented limitations:** none of this is a resolution issue — it is a temporal-currency and areal-unit-mismatch issue.
**Resolution_fit:** To reach 150m would require dasymetric interpolation (splitting ward totals using building footprint/land cover as ancillary weights, the same class of method WorldPop uses) — this must be built and documented in-house; no ready 150m product exists.
**Verdict:** not_useful (primary — both because of 2011-era staleness and total resolution mismatch).

**Fallback 1 — Municipal/TNSCB slum boundary maps:** Native resolution would be settlement-polygon level if obtained, finer than ward but still not a grid, and public accessibility could not be confirmed (no standing open-data portal identified). Does **not** resolve the primary failure — it substitutes settlement-type proxy for actual age/occupation attributes, and carries its own unconfirmed-availability problem.
**Fallback 2 — WorldPop age/sex-structure gridded layers:** Native 100m, same dasymetric-model caveat as Dataset 4 (modeled, not measured), and does not carry occupation data at all — only partially mitigates (age-structure component only).

**Methodological workaround (no fallback closes the gap):** Combine WorldPop age-structure 100m layers (occupation excluded, flagged as a residual gap) with a targeted primary field survey or NFHS-5 district-level occupation indicators statistically apportioned via dasymetric weighting against building density; report uncertainty bounds explicitly rather than presenting the result as census-grade.
**Final recommendation:** No source is native or current enough. **Resolution honesty check: any 150m demographic-vulnerability surface for this corridor is necessarily a modeled/interpolated construct built on 2011-era inputs — it must be labeled as an estimate with stated uncertainty, never presented as measured 2015-2026 demographic data.**

---

## 6. Building footprint / building type
**Intended use:** Built-form density and heat-retention proxy per cell.
**Primary source:** OpenStreetMap, OSM contributors. Access: Overpass API / Geofabrik extracts.
**TRUE native resolution:** Vector polygon data — no fixed grid resolution; footprint vertex precision is effectively sub-meter (GPS/imagery-traced), but **coverage completeness**, not resolution, is the real constraint.
**Temporal coverage:** Continuously edited since OSM's inception; no fixed historical snapshots unless full-history extracts are pulled — full 2015-2026 window is representable only for the *current* state, not a validated time series (OSM does not maintain agency-validated annual releases).
**Documented limitations:** OSM's own data-quality documentation (OSM Wiki, "Editing Standards and Conventions") notes crowd-sourced completeness varies by mapper activity; no formal completeness audit for Chennai's periphery is published by OSM itself.
**Resolution_fit:** Vector precision exceeds 150m needs; the risk is coverage gaps, not resolution.
**Verdict:** useful_with_caveats.
**Final recommendation:** OSM building layer. **Resolution honesty check: genuinely native vector precision (no interpolation involved) — the caveat is completeness/currency, not resolution.**

---

## 7. Road network
**Intended use:** Impervious linear infrastructure / street-canyon geometry per cell.
**Primary source:** OpenStreetMap, same access as above.
**TRUE native resolution:** Vector, sub-meter geometric precision; same caveat structure as building footprints.
**Temporal coverage:** Continuous, current-state only.
**Documented limitations:** minor/service road completeness lags in newly built areas (OSM Wiki completeness discussions).
**Resolution_fit:** Exceeds 150m requirement.
**Verdict:** useful.
**Final recommendation:** OSM road network. **Resolution honesty check: native vector precision, no interpolation.**

---

## 8. Water body / blue infrastructure
**Intended use:** Map perennial/seasonal water including Pallikaranai marsh.
**Primary source:** JRC Global Surface Water, EC Joint Research Centre. Access: GEE `JRC/GSW1_4`, or EC JRC Data Portal.
**TRUE native resolution:** **30m**, directly inherited from the Landsat archive it is derived from (JRC's own technical documentation, Pekel et al. 2016, Nature) — this is a genuine sensor-native resolution, not an interpolated display value.
**Temporal coverage:** 1984-2021 (v1.4) — does not extend to 2026; confirm whether a newer JRC version has been released before final use.
**Documented limitations (Pekel et al. 2016, JRC GSW technical note):** optical water detection under dense reed/vegetation cover is known to underestimate wetland water extent — directly relevant to the vegetated Pallikaranai marsh.
**Resolution_fit:** 30m aggregates into 150m cleanly, but the marsh detection gap is a coverage/accuracy issue, not resolution.
**Verdict:** not_useful (for this specific pilot site's dominant water feature).

**Fallback — Sentinel-2 NDWI-derived mask:** TRUE native resolution 10m (B3/B8, both natively 10m, no upsampling). Resolves the primary failure **partially**: finer resolution better resolves the fragmented marsh/reed mosaic, but NDWI still confuses mixed water-vegetation pixels — requires user-calibrated threshold validated against known marsh extent, documented as a project-specific calibration, not a validated agency product.
**Final recommendation:** Sentinel-2 NDWI mask, calibrated. **Resolution honesty check: native 10m sensor data; the "improvement" over JRC GSW is a genuine resolution gain (30m→10m), not an interpolation trick — but the NDWI threshold itself is an uncalibrated derivation until validated.**

---

## 9. Air temperature
**Intended use:** Near-surface air temperature reference per cell.
**Primary source:** IMD station network. Access: IMD data portal / NDC data requests.
**TRUE native resolution:** Point observations only — no grid. Chennai has effectively 2-3 long-record stations (Meenambakkam, Nungambakkam) across the entire metro area.
**Documented limitations (IMD station metadata):** stations sited for aviation/synoptic purposes, not for intra-urban heat-island resolution.
**Resolution_fit:** Cannot be interpolated to 150m without enormous uncertainty over a ~30km corridor with only 2-3 points.
**Verdict:** not_useful.

**Fallback — ERA5-Land reanalysis, ECMWF/Copernicus CDS:** Vendor/GEE listings often present this as a "gridded" or "high-resolution" reanalysis. **Tracing to source:** ERA5-Land's documented native resolution (Copernicus Climate Data Store technical documentation, Muñoz-Sabater et al. 2021, ESSD) is **0.1° x 0.1° ≈ 9km**, itself downscaled by ECMWF from the ~31km native ERA5 atmospheric model via a fixed physiographic lapse-rate correction — it is NOT a direct 9km observation, and it is absolutely not native at 150m or 1km. **This is exactly the "high-resolution smoothed from a coarser model" pattern the audit is designed to catch — flagged accordingly.** Does **not** resolve the primary failure: a 9km cell (~31 km² area) is ~1,400x the area of a 150m cell and cannot differentiate anything within the OMR-Pallikaranai corridor.

**Methodological workaround:** Build an empirical LST-to-air-temperature regression/calibration model (a documented approach in urban climate literature, e.g. Anderson et al. 2020-style Ts-Ta relationships) using the 30m/150m-aggregated Landsat LST (Dataset 1) as the spatial predictor, calibrated against the sparse IMD station records as ground truth, with a residual-based uncertainty band reported per cell — explicitly presented as a *statistically derived proxy*, not a measured temperature.
**Final recommendation:** No source is native at 150m. **Resolution honesty check: neither IMD (point) nor ERA5-Land (~9km model-native, itself a downscale of a ~31km atmospheric model) provides anything close to 150m nativeness; any 150m air-temperature layer must be disclosed as an LST-calibrated statistical proxy with uncertainty bounds, never presented as measured air temperature.**

---

## 10. Wind speed/direction
**Intended use:** Regional wind forcing for heat dispersion/ventilation-corridor context.
**Primary source:** ERA5-Land, ECMWF/Copernicus CDS.
**TRUE native resolution:** Same as above — **0.1° ≈ 9km**, itself derived from the ~31km native ERA5 atmospheric reanalysis; explicitly not a 150m or 1km product no matter how it is displayed or interpolated in visualization tools.
**Documented limitations (Copernicus CDS ERA5-Land documentation):** does not resolve building-induced turbulence or street-canyon channeling; intended for land-surface/hydrology applications at regional scale, not intra-urban wind mapping.
**Resolution_fit:** Cannot be downscaled to 150m with any defensible method without local wind observations to calibrate against (none identified for this corridor) — cell-level wind differentiation within OMR-Pallikaranai is not achievable from this source.
**Verdict:** useful_with_caveats, but only as **regional boundary-condition context**, explicitly not as a 150m-differentiated attribute.
**Final recommendation:** ERA5-Land, used strictly as background forcing. **Resolution honesty check: ~9km model-native data — using it as if it varies meaningfully between adjacent 150m cells would be a false-precision claim. If cell-level wind differentiation is required for the pipeline, no genuinely native source currently exists for this corridor, and that gap should be stated rather than papered over.**

---

## Summary Table

| # | Dataset | True native res. | 150m-native? | Verdict |
|---|---|---|---|---|
| 1 | LST | 30m (distributed from 100m sensing) | Yes | useful_with_caveats |
| 2 | LULC | 10m | Yes | useful_with_caveats |
| 3 | NDVI | 10m | Yes | useful |
| 4 | Population | 100m (**modeled**, not measured) | Yes (as a model output) | useful_with_caveats |
| 5 | Demographic vulnerability | None (ward polygons, 2011) | **No** — requires disclosed interpolation | not_useful / unresolved |
| 6 | Building footprint | Vector (sub-m) | Yes | useful_with_caveats |
| 7 | Road network | Vector (sub-m) | Yes | useful |
| 8 | Water body | 10m (Sentinel-2 NDWI, calibrated) | Yes | not_useful → use fallback |
| 9 | Air temperature | ~9km (ERA5) / point (IMD) | **No** | not_useful / proxy required |
| 10 | Wind | ~9km (ERA5) | **No** | useful_with_caveats (regional context only) |

**Bottom line:** 150m is achievable natively for 7 of 10 datasets. Two (air temperature, wind) are hard-blocked by ERA5-Land's genuine ~9km native grid — no vendor framing changes that physics, and no downscaling method can honestly manufacture 150m precision from it without independent local calibration data, which doesn't currently exist for this corridor. One (demographic vulnerability) is blocked by Census 2011's polygon structure and 15-year staleness. All three are flagged with explicit proxy/workaround paths and required uncertainty disclosure rather than a false "resolved" status.
