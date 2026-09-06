# Intervention classification

The complete feature table was split into two exclusive intervention tracks.

## Rules

Rows are classified as `retrofit` when either condition is true:

- `building_density >= 0.60`, or
- `ndbi >= -0.04` and `sky_view_factor <= 0.40`

All remaining rows are classified as `openspace`. This is an initial rule-based
screening classification, not a validated land-use map. `building_density`,
`ndbi`, and `sky_view_factor` are proxies; open-space suitability should be
validated against land-use or vacant-land data before final decisions.

The original unclassified feature table is preserved. The classified master
table contains `intervention_type` and `intervention_rule` columns, and the two
filtered CSV files contain the same feature columns plus those labels.
